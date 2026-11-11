# minigbm：给 sun4i-drm 加后端，并让 cros_gralloc 找到 card 节点

sd47 的改动。核心两件事，外加两处打包/标签的补漏。

## 为什么（真因）

SurfaceFlinger 每次启动都死在 `gralloc-mapper is missing`，HWC 也没画过一帧。
顺着日志查到 mapper 这一侧有两个断点，**两个都得补**：

1. **`cros_gralloc_driver::init()` 找不到节点。** 它只扫 `/dev/dri/renderD128..190`，
   而 sun4i-drm 的 `drm_driver` 只有 `DRIVER_GEM|DRIVER_MODESET|DRIVER_ATOMIC`、
   **没有 `DRIVER_RENDER`**，所以这块板子只有 `card0`，根本没有 render 节点。
   init 必然返回 `-ENODEV`。
2. **就算拿到 card0，也没有后端认它。** `external/minigbm` 里没有 sun4i，也没有
   panfrost（`grep -rn panfrost external/minigbm` 命中 0）。
   `drv_get_backend()` 拿 `drmGetVersion()->name`（`"sun4i-drm"`）去 `backend_list[]`
   里 `strcmp`，匹配不上就 `goto free_driver`。

于是 `HIDL_FETCH_IMapper` 虽然无条件 `new CrosGralloc4Mapper`（所以 `.so` 加载看起来
是"成功"的），但 `mDriver` 是空的，任何 allocate 都返回 `NO_RESOURCES`，
SurfaceFlinger 在 `GraphicBufferMapper` 构造函数里 `LOG_ALWAYS_FATAL`。

## 为什么是 sun4i 后端而不是 panfrost 后端

- sun4i-drm 是 `DRM_GEM_DMA` 驱动，`DRM_IOCTL_MODE_CREATE_DUMB` 给的是物理连续的
  CMA 缓冲，而 `drm_gem_dma_prime_import_sg_table` 要求单段连续——正好是显示引擎能
  扫的东西，也能导出成 dma-buf 给别处导入。
- panfrost 是 render-only + shmem，没有 `dumb_create`，它的 BO 不保证连续，sun4i
  导入会 `-EINVAL`。而且这块板的 EGL 是 SwiftShader（`ro.hardware.egl=swiftshader`），
  渲染在 CPU 上，GPU 根本不在分配路径里。

所以分配交给显示控制器，是这里唯一说得通的方向。

## 改了什么

- `sun4i.c`（新增）：`backend_sun4i`，`.name = "sun4i-drm"`，
  `bo_create = drv_dumb_bo_create`，`bo_import = drv_prime_bo_import`，
  `bo_map = drv_dumb_bo_map`，`bo_destroy = drv_dumb_bo_destroy`；
  RGB 六种格式挂 `BO_USE_RENDER_MASK|BO_USE_SCANOUT`，YUV/R8 挂 `BO_USE_TEXTURE_MASK`，
  末尾 `drv_modify_linear_combinations()` 让 XRGB/ARGB 能当 cursor 和 scanout。
- `Android.bp`：`minigbm_defaults.srcs` 加 `"sun4i.c"`；`gralloc.minigbm` 加
  `relative_install_path: "hw"`。
- `drv.c`：`extern const struct backend backend_sun4i;` + `&backend_sun4i,`。
- `cros_gralloc/cros_gralloc_driver.cc`：`init()` 先扫 `%s/renderD%d`（128..190），
  再扫 `%s/card%d`（0..62），仍然过滤 `vgem`；成功时打
  `render_node_scan: using <node>`，这条串是判读用的标记。

## 配套的 device 侧改动（同一提交）

- `device/anbernic/rg34xxsp/device.mk`：`PRODUCT_PACKAGES` 加 `gralloc.minigbm`。
  这个模块在 `external/minigbm/Android.bp` 里一直存在，但从来没进过包列表——
  也就是说 `/vendor/lib64/hw/gralloc.minigbm.so` 在镜像里根本不存在。
- `device/anbernic/rg34xxsp/sepolicy/vendor/file_contexts`：给
  `allocator@4.0-service.minigbm`、`gatekeeper@1.0-service.software`、
  `gralloc.minigbm.so`、`mapper@4.0-impl.minigbm.so` 补标签。平台规则只写到
  `allocator@4.0-service`（没有 `.minigbm` 后缀），所以这两个二进制之前一直停在
  `u:r:init:s0`，allocator 因此注册不上 hwbinder 服务（kmsg 里
  `Unable to register ... Couldn't register binder service`）。

## 一处需要说明的误判

logcat 里那条

```
I hwc-platform-drm-generic: Using Graphics Memory Allocator Module gralloc module: The Android Open Source Project
```

**不能**用来断定 HWC 选错了 importer。`platformdrmgeneric.cpp` 同时编进了
`hwcomposer.drm_minigbm`，而 `DrmMinigbmImporter` 继承 `DrmGenericImporter` 且不重写
`Init()`，所以这行 LOG_TAG 是 `hwc-platform-drm-generic` 的日志其实来自 minigbm
importer 继承来的 `Init()`。它只说明 `hw_get_module()` 回退到了 AOSP 的
`gralloc.default`（因为 `gralloc.minigbm.so` 没装），而 `DrmMinigbmImporter::ConvertBoInfo`
是重写过的、按 `cros_gralloc_handle` 解析，所以 HWC 的 buffer 解析并没有错。
补上 `gralloc.minigbm` 是为了消除这个回退，而不是因为它是当前卡死的原因。

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
如果出现 `DRM_IOCTL_MODE_CREATE_DUMB failed`，说明后端选对了但 dumb buffer 被拒，
下一步要看 sun4i 的 `dumb_create` 是否要求 pitch 对齐。
