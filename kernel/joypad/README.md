# rocknix-joypad（第三方外部模块，本仓库内置副本）

## 为什么这个目录存在

RG34XX-SP 的手柄（D-pad + ABXY + L1/L2/R1/R2 + 双摇杆 + L3/R3 + MENU）
**不在内核树里**。DTS 里那个 `compatible = "rocknix-singleadc-joypad"` 对应的驱动
是 ROCKNIX 以**树外模块**形式维护的：

- 包描述：`projects/ROCKNIX/packages/linux-drivers/rocknix-joypad/package.mk`
- 源码地址：<https://github.com/hackbar/rocknix-joypad>
- 固定版本：`005033d1ddd8746c771f2c81f1174f2d892541c9`
- 包内 sha256：`7a9a90d9d25d48384771bf8fe7cfb53bba708b64aa4e56d44e71205a8bcc699a`

所以在 ROCKNIX 仓库里搜 `.c` 是搜不到的（本鱼在这儿绕了很久），
只能从上面那个 URL 取。本目录就是那份源码的副本，`tools/build-kernel.sh`
会把它塞进内核树 `drivers/input/joystick/` 当**内建（builtin）**编，而不是做成 .ko：

> Android 上优先内建。模块要考虑 `/vendor/lib/modules` 的挂载时序、依赖顺序、
> `modprobe` 谁去调；内建省掉这一整类问题，代价只是 Image 大几 KB。

## 文件

| 文件 | 说明 |
|---|---|
| `rocknix-singleadc-joypad.c` | H700 用的那个（单 ADC 多路复用读摇杆 + gpio 读按键） |
| `rocknix-joypad.h` | SARADC 通道常量 |
| `CREDITS` | 上游致谢（Originated from Hardkernel odroid-gou-joypad） |

上游包里的 `rocknix-joypad.c`（SPI MCU 版，S922X/RK3588 用）本仓库**不需要**，
没有收录：`package.mk` 的 Makefile 里 `DEVICE=H700` 时只编 `rocknix-singleadc-joypad.o`。

## 两条硬依赖（缺一条就编不过或加载失败）

### 1. `joypad_input_g` —— 来自 ROCKNIX 的通用 adc-keys 补丁

驱动里是这样用的：

```c
/* rocknix-singleadc-joypad.c:232 */
extern struct input_dev * joypad_input_g;
...
/* rocknix-singleadc-joypad.c:849 */
joypad_input_g = input;
```

赋值是**无条件**的，但符号的**定义**在别处 ——
`projects/ROCKNIX/packages/linux/patches/mainline/0004-input-adc-keys-redirect-keycode-316-to-rocknix-joypa.patch`：

```c
/* drivers/input/keyboard/adc-keys.c */
struct input_dev * joypad_input_g;
EXPORT_SYMBOL(joypad_input_g);

void rk_send_key_f_key_up(void)   { ... input_report_key(joypad_input_g, BTN_MODE, 1); ... }
void rk_send_key_f_key_down(void) { ... input_report_key(joypad_input_g, BTN_MODE, 0); ... }
```

它同时把 `adc_keys_poll()` 里 keycode 316（`BTN_MODE`）的按键转给手柄，
避免 MENU 键被 ADC 键盘和手柄各报一次。

**这条补丁必须打**，而且要保证 `CONFIG_KEYBOARD_ADC=y`（ROCKNIX 的 H700 config 里已经是 `=y`）。
不打的表现：内建时报 `undefined symbol joypad_input_g`；做 .ko 时 insmod 失败。

### 2. PWM 与 IIO

- `devm_pwm_get(dev, "enable")` 取震动马达的 PWM（DTS 里 `pwms = <&pwm 2 1000000 0>`），
  需要 `CONFIG_PWM=y` + DTS 有 `&pwm` 节点（补丁 0008 提供）。
- `devm_iio_channel_get()` 读摇杆 ADC（`io-channels = <&gpadc 0>`），
  需要 `CONFIG_IIO=y`，并 `MODULE_IMPORT_NS("IIO_CONSUMER")`。
- `input_ff_create_memless()` 提供 FF_RUMBLE，需要 `CONFIG_INPUT_FF_MEMLESS`。

内核 7.1.2 上 PWM API 走的是 `#if (LINUX_VERSION_CODE < KERNEL_VERSION(6, 11, 0))`
的 `#else` 分支（`pwm_apply_might_sleep`），无需改动。

## 配置符号

本仓库在 `kernel/fragment.config` 里开：

```
CONFIG_INPUT_ROCKNIX_SINGLEADC_JOYPAD=y
```

注意：这个符号**是 build-kernel.sh 自己插进 `drivers/input/joystick/Kconfig` 的**
（和 panel-mipi 一样，没有任何上游 Kconfig 定义它）。
`olddefconfig` 对没有 Kconfig 条目的符号会**直接丢掉**，
所以顺序必须是「先插 Kconfig 条目，再 merge fragment」。

## 与 stock 固件的差异

stock 是全志 BSP 的 `gpio-keys-polled`（label `ANBERNIC-keys`），
D-pad 用 `ABS_HAT0X/ABS_HAT0Y`，A/B/X/Y 是 `BTN_SOUTH/EAST/NORTH/WEST`，
和 ROCKNIX 这套（D-pad 单独一组、A=`BTN_EAST`）**映射方向相反**。
本仓库跟 ROCKNIX 走，`keylayout/Vendor_484b_Product_14df.kl` 已按此对齐。
