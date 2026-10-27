[ENGLISH](README_en.md)

# Open WebUI 优化版 Chrome

为 Open WebUI 准备的定制安卓浏览器: 把 Chromium 官方构建的 `ChromePublic.apk` 改造成一个专门用来打开 Open WebUI 的 Chromium, 解决 Open WebUI 在手机 Chrome 上的几个卡顿问题。输出是一个普通的 apk, 任何安卓设备直接安装即可, 不需要 root、adb 或其它工具, 和手机上原有的 Chrome 互不影响。

## 背景与动机

我们的 Open WebUI 前端为了保证滚动、跳转的准确, 会把 chat 里的全部消息完整渲染, 消息多的 chat 页面元素以万计。在这个前提下, Chrome 有两类行为会造成明显卡顿, 而且都没法在页面或浏览器设置里稳定地关掉。

**1. 无障碍被自动开启, 开关弹窗卡两三百毫秒**

Chrome 默认不开无障碍, 但只要系统里有程序读取页面信息 (输入法、自动填充/密码管理服务、部分安全或远程控制软件等), 就会自动开启。开启后页面每次变化都要同步更新一棵和页面一样大的无障碍树, 消息多的 chat 里每次打开、关闭弹窗 (模型选择、暂存板等) 都会卡两三百毫秒。很多手机默认就开着这类服务, 用户一般察觉不到。在 `chrome://accessibility` 里可以临时关掉, 但重启浏览器就会失效, 又被重新开启。

**2. 滑动掉帧取决于 Google 给这台设备分的实验组**

消息多时页面滚动由主线程执行。Chrome 对触摸滑动有几项优化 (touchmove 不节流、滚动事件重采样、滚动预测), 但是否开启由 Google 服务器按设备随机下发的实验配置决定: 同一版本的 Chrome, 有的手机滑动流畅, 有的明显掉帧, 用户无法选择。

**3. 给正式版 Chrome 加启动参数不可行**

上面两个问题本可以用启动参数解决, 但正式版 Chrome 在安卓上只能通过 adb 往设备里写命令行文件来加参数, 每台设备都要连电脑操作, 太麻烦; 而且加了参数之后, 每次打开浏览器都会弹出"使用了不受支持的命令行标记"的警告。

**4. Chromium 开发构建自带的实验配置**

直接用 Chromium 官方构建也不行: 它会打开源码里自带的一整套测试用实验 (例如底部工具栏这类实验界面), 界面和行为都和正式版 Chrome 差别很大。

## 改动内容

在 Chromium 启动时把下面这些参数写死, 不需要 adb, 也不会出现警告:

- **实验配置固定为一份滑动流畅的配置**: 原样使用一台滑动流畅的手机 (小米 18) 上 Chrome 155 实际下发的实验配置, 其中包含上面那几项滑动优化。其它功能也因此尽量接近正式版 Chrome。
- **关闭 Chromium 自带的测试实验配置** (`--disable-field-trial-config`), 避免开发构建的实验界面。
- **关闭网页的无障碍** (`--disable-renderer-accessibility`)。

另外直接切断无障碍的入口: 系统 (以及开着自动填充服务时浏览器自己) 向网页要无障碍接口的那个方法改为永远返回空, 网页不会再建立和维护无障碍树。

代价:
- 没有 Google 账号同步、网页翻译等依赖 Google 服务的功能;
- 版本来自 Chromium 官方快照 (目前用的是 156.0.8078.0), 不是正式版 Chrome。

## 使用方法

```bash
./build.sh                                   # origin/ChromePublic.apk -> out/ChromePublic_patched.apk
python3 -u build.py <输入apk> <输出apk>       # 指定输入输出
```

输入是 Chromium 官方快照里的 `ChromePublic.apk` (从 https://commondatastorage.googleapis.com/chromium-browser-snapshots/index.html?prefix=Android_Arm64/ 下载 `chrome-android.zip`, 里面的 `apks/ChromePublic.apk`)。需要 root 权限 (用 chroot) 和 Linux x86_64。

## 实现概要

- `build.py` 把编译环境镜像 (`image/chrome_edit_env.tar.7z`, Rocky 9 + JRE + smali + zipalign/apksigner) 解开到临时目录, 在 chroot 里运行 `patch/patch_apk.py`: 反编译 dex、在命令行初始化处注入参数、切断无障碍入口、重新打包并签名。镜像只含环境, 转换代码和配置都在镜像外; `image/make_image.py` 可以从固定版本、固定校验值的官方包重新构建镜像。
- 配置在 `config/`: 参数和实验配置文件 (`config.json`、`variations_xiaomi18_chrome155.json`), 以及签名密钥 `keystore.jks`。以后的版本必须用同一个密钥签名才能覆盖安装。
- 注入点和 Chromium 的具体版本有关, 换新版本时如果对不上, 脚本会报错退出, 需要重新定位。
