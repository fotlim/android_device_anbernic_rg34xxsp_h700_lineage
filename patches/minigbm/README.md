# minigbm：给 sun4i-drm 加后端，并让 cros_gralloc 找到 card 节点

sd47 的一次性改动，三件事，缺一不可。

## 为什么

SurfaceFlinger 每次启动都死在 `gralloc-mapper is missing`，HWC 也没画过一帧。
顺着日志查到两条独立的断点：

1. **mapper 拿不到 driver。** `cros_gralloc_driver::init()` 只扫 `/dev/dri/renderD128..190`，
   而 sun4i-drm 的 `drm_driver` 只有 `DRIVER_GEM|DRIVER_MODESET|DRIVER_ATOMIC`、没有
   `DRIVER_RENDER`，所以这块板子**只有 card0，没有 render 节点**。init 必然 `-ENODEV`。
2. **就算拿到 card0，也没有后端认它。** `external/minigbm` 里没有 sun4i，也没有 panfrost
   （`grep -rn panfrost external/minigbm` 命中 0）。`drv_get_backend()` 拿
   `drmGetVersion()->name`（`"sun4i-drm"`）去 `backend_list[]` 里 strcmp，匹配不上就
   `goto free_driver`。

于是 `HIDL_FETCH_IMapper` 虽然无条件 `new CrosGralloc4Mapper`（所以 `.so` 加载是"成功"的），
但 `mDriver` 是空的，任何 allocate 都返回 `NO_RESOURCES`，SurfaceFlinger 在
`GraphicBufferMapper` 构造函数里 `LOG_ALWAYS_FATAL`。

## 为什么是 sun4i 后端而不是 panfrost 后端

- sun4i-drm 是 `DRM_GEM_DMA` 驱动，`DRM_IOCTL_MODE_CREATE_DUMB` 给的是物理连续的 CMA
  缓冲，`drm_gem_dma_prime_import_sg_table` 要求单段连续——正好是显示引擎能扫的东西，
  也能导出成 dma-buf 给别处导入。
- panfrost 是 render-only + shmem，没有 `dumb_create`，它的 BO 不保证连续，sun4i 导入会
  `-EINVAL`。而且这块板的 EGL 是 SwiftShader（`ro.hardware.egl=swiftshader`），渲染在
  CPU 上，GPU 根本不在分配路径里。

所以分配交给显示控制器，是这里唯一说得通的方向。

## 改了什么

- `sun4i.c`（新增）：`backend_sun4i`，`.name = "sun4i-drm"`，`bo_create = drv_dumb_bo_create`，
  `bo_import = drv_prime_bo_import`，`bo_map = drv_dumb_bo_map`，`bo_destroy =
  drv_dumb_bo_destroy`；RGB 六种格式挂 `BO_USE_RENDER_MASK|BO_USE_SCANOUT`，YUV/R8 挂
  `BO_USE_TEXTURE_MASK`，末尾 `drv_modify_linear_combinations()` 让 XRGB/ARGB 能当 cursor
  和 scanout。
- `Android.bp`：`minigbm_defaults.srcs` 加 `"sun4i.c"`；`gralloc.minigbm` 加
  `relative_install_path: "hw"`——`hw_get_module()` 只在 `<lib>/hw` 里找，少了这一行模块
  会落到 `/vendor/lib64/` 而加载器静默回退到 AOSP 的 `gralloc.default` 空壳。
- `drv.c`：`extern const struct backend backend_sun4i;` + `&backend_sun4i,`。
- `cros_gralloc/cros_gralloc_driver.cc`：`init()` 先扫 `%s/renderD%d`（128..190），再扫
  `%s/card%d`（0..62），仍然过滤 `vgem`；成功时打 `render_node_scan: using <node>`，
  这条串是判读用的标记。

## 配套的 device 侧改动

不在这个目录里，但必须一起进去：

- `device/anbernic/rg34xxsp/device.mk`：`PRODUCT_PACKAGES` 加 `gralloc.minigbm`
  （之前根本没装）。
- `device/anbernic/rg34xxsp/sepolicy/vendor/file_contexts`：给
  `allocator@4.0-service.minigbm`、`gatekeeper@1.0-service.software`、
  `gralloc.minigbm.so`、`mapper@4.0-impl.minigbm.so` 补标签。平台规则只写到
  `allocator@4.0-service`（没有 `.minigbm` 后缀），所以这两个二进制之前一直停在
  `u:r:init:s0`，allocator 因此注册不上 hwbinder 服务。

## 怎么用

```bash
git -C external/minigbm apply patches/minigbm/minigbm-graphics.patch
cp patches/minigbm/sun4i.c external/minigbm/sun4i.c
```

## 判读

启动后 kmsg 里应该出现：

```
render_node_scan: using /dev/dri/card0
```

以及不再有 `gralloc-mapper is missing`，`drm_atomic_commit` 计数 > 0。
