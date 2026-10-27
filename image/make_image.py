#!/usr/bin/env python3
# 构建编译环境镜像 (chroot 用的根文件系统 tar): Rocky 9 容器基础镜像 + JRE 21 + smali/baksmali + build-tools 里的 zipalign/apksigner。
# 镜像里只有环境, 不含转换代码和配置; 产物 image/chrome_edit_env.tar, 再用 7z 压缩成 image/chrome_edit_env.tar.7z (这个进 git)
# 用法: python3 -u image/make_image.py [--tmp 临时目录基址, 默认 /dev/shm]
import os, sys, json, glob, shutil, hashlib, tarfile, zipfile, tempfile, argparse, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'image'))
import mdl

DL = os.path.join(ROOT, 'image/downloads')
TAR = os.path.join(ROOT, 'image/chrome_edit_env.tar')
TAR_7Z = TAR + '.7z'
SEVEN_ZIP = os.path.join(ROOT, 'host_tools/7zz')
# 固定版本 + 校验值 (Rocky、JRE 用官方公布的 sha256; 其余为首次下载时记录的值)
FILES = {
    'rocky9_base.oci.tar.xz': ('https://dl.rockylinux.org/pub/rocky/9/images/x86_64/Rocky-9-Container-Base-9.8-20260525.0.x86_64.oci.tar.xz',
                               '1210df99dcf0ef4d73940244e6d703935175629598b09c0e430da20e76768402'),
    'jre21.tar.gz': ('https://api.adoptium.net/v3/binary/version/jdk-21.0.13%2B9/linux/x64/jre/hotspot/normal/eclipse',
                     'a535a1df9c6284d9618872984e026ae0352d1f7e9e0605425c0e6114f47351dd'),
    'smali-2.5.2.jar': ('https://bitbucket.org/JesusFreke/smali/downloads/smali-2.5.2.jar',
                        '9544299578b16f771d8aa8eaefe0d3718ca03478c16f3c356f2fcf1366bfb116'),
    'baksmali-2.5.2.jar': ('https://bitbucket.org/JesusFreke/smali/downloads/baksmali-2.5.2.jar',
                           'd3116248cce4f82ec5a31eb7f95ee75daff42ddf6eed0ab573973dc53fbad2e5'),
    'build-tools_r36.1_linux.zip': ('https://dl.google.com/android/repository/build-tools_r36.1_linux.zip',
                                    'a7b5889e4a79fcf3b0976bef40d401f4240fb1eed891d9d91169da1111e11d78'),
    '7z2501-linux-x64.tar.xz': ('https://www.7-zip.org/a/7z2501-linux-x64.tar.xz',
                                '4ca3b7c6f2f67866b92622818b58233dc70367be2f36b498eb0bdeaaa44b53f4'),
}
# chroot 里执行命令统一用的环境: 清掉宿主机的环境变量; chroot 里没有 /proc, 动态链接器算不出 $ORIGIN,
# java 启动器 ($ORIGIN/../lib) 和 zipalign ($ORIGIN/lib64) 都找不到自己的库, 显式给出库路径
CHROOT_ENV = 'env -i PATH=/usr/bin:/bin HOME=/root LANG=C.UTF-8 LD_LIBRARY_PATH=/opt/jre/lib:/opt/jre/lib/server:/opt/build-tools/lib64'
# build-tools 里只取用得到的: zipalign 依赖同目录 lib64 下的 libc++.so (RUNPATH 指向那里), apksigner 只要 jar
BUILD_TOOLS_KEEP = ('android-16/zipalign', 'android-16/lib64/libc++.so', 'android-16/lib/apksigner.jar')


def fail(msg):
    print(f'❌ {msg}', file=sys.stderr)
    raise SystemExit(1)  # 不用 os._exit: 要让 finally 删掉 /dev/shm 里的临时目录


def run(cmd):
    print(f'ℹ️ {cmd}', file=sys.stderr)
    r = subprocess.run(cmd, shell=True)
    if r.returncode != 0:
        fail(f'命令失败 ({r.returncode}): {cmd}')


def fetch(name):
    path = os.path.join(DL, name)
    url, sha = FILES[name]
    if not os.path.exists(path):
        os.makedirs(DL, exist_ok=True)
        mdl.main(url, path)
    h = hashlib.sha256(open(path, 'rb').read()).hexdigest()
    if h != sha:
        fail(f'{name} 校验值不符: 期望 {sha}, 实际 {h} (删掉 {path} 重新下载)')
    return path


def ensure_7zz():
    if os.path.exists(SEVEN_ZIP):
        return SEVEN_ZIP
    os.makedirs(os.path.dirname(SEVEN_ZIP), exist_ok=True)
    with tarfile.open(fetch('7z2501-linux-x64.tar.xz')) as t:
        open(SEVEN_ZIP, 'wb').write(t.extractfile('7zz').read())
    os.chmod(SEVEN_ZIP, 0o755)
    return SEVEN_ZIP


def chroot_check(rootfs, cmd, expect):
    r = subprocess.run(f'chroot {rootfs} {CHROOT_ENV} /bin/sh -c "{cmd}"', shell=True, capture_output=True, text=True)
    out = r.stdout + r.stderr
    if expect not in out:
        fail(f'镜像自检失败: {cmd} (退出码 {r.returncode})\n{out}')
    print(f'✅ 自检 {cmd}: {out.strip().splitlines()[0]}', file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description='构建 chrome_edit 编译环境镜像')
    ap.add_argument('--tmp', default='/dev/shm', help='临时目录基址, 其下创建随机子目录, 结束后删除')
    args = ap.parse_args()
    for name in FILES:
        fetch(name)
    work = tempfile.mkdtemp(prefix='chrome_edit_img_', dir=args.tmp)
    rootfs = os.path.join(work, 'rootfs')
    try:
        # Rocky 容器镜像是 OCI 格式, 只有一层, 取出层的 tar.gz 后用系统 tar 解开 (保留权限和属主)
        with tarfile.open(os.path.join(DL, 'rocky9_base.oci.tar.xz')) as t:
            idx = json.load(t.extractfile('index.json'))
            mf = json.load(t.extractfile('blobs/sha256/' + idx['manifests'][0]['digest'].split(':')[1]))
            if len(mf['layers']) != 1:
                fail(f'Rocky 镜像层数应为 1, 实际 {len(mf["layers"])}')
            layer = t.extractfile('blobs/sha256/' + mf['layers'][0]['digest'].split(':')[1])
            with open(os.path.join(work, 'layer.tar.gz'), 'wb') as f:
                shutil.copyfileobj(layer, f)
        os.makedirs(rootfs)
        run(f'tar -xzpf {work}/layer.tar.gz --numeric-owner -C {rootfs}')
        run(f'tar -xzf {DL}/jre21.tar.gz -C {rootfs}/opt')
        jre = glob.glob(f'{rootfs}/opt/jdk-21*-jre')
        if len(jre) != 1:
            fail(f'JRE 目录应恰好 1 个, 实际 {jre}')
        os.rename(jre[0], f'{rootfs}/opt/jre')
        os.makedirs(f'{rootfs}/opt/smali')
        shutil.copy(f'{DL}/smali-2.5.2.jar', f'{rootfs}/opt/smali/smali.jar')
        shutil.copy(f'{DL}/baksmali-2.5.2.jar', f'{rootfs}/opt/smali/baksmali.jar')
        with zipfile.ZipFile(f'{DL}/build-tools_r36.1_linux.zip') as z:
            for n in BUILD_TOOLS_KEEP:
                dst = f'{rootfs}/opt/build-tools/' + n.split('/', 1)[1]
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                open(dst, 'wb').write(z.read(n))
        os.chmod(f'{rootfs}/opt/build-tools/zipalign', 0o755)
        chroot_check(rootfs, 'python3 --version', 'Python 3')
        chroot_check(rootfs, '/opt/jre/bin/java -version', 'openjdk version "21')
        chroot_check(rootfs, '/opt/jre/bin/java -jar /opt/smali/smali.jar --version', 'smali 2.5.2')
        chroot_check(rootfs, '/opt/jre/bin/java -jar /opt/smali/baksmali.jar --version', 'baksmali 2.5.2')
        chroot_check(rootfs, '/opt/jre/bin/java -jar /opt/build-tools/lib/apksigner.jar version', '.')
        chroot_check(rootfs, '/opt/build-tools/zipalign', 'Zip alignment utility')
        # 镜像的 /dev 是空的 (容器里没有 mknod 权限建设备节点), 实际生成一次密钥, 确认 Java 的安全随机数在这种环境下可用
        chroot_check(rootfs, '/opt/jre/bin/keytool -genkeypair -keystore /tmp/selftest.jks -storepass selftest -keypass selftest '
                     '-alias t -keyalg RSA -keysize 2048 -dname CN=t && /opt/jre/bin/keytool -list -keystore /tmp/selftest.jks '
                     '-storepass selftest && rm /tmp/selftest.jks', 'PrivateKeyEntry')
        run(f'tar -C {rootfs} --numeric-owner -cpf {TAR} .')
        if os.path.exists(TAR_7Z):
            os.remove(TAR_7Z)
        run(f'{ensure_7zz()} a -mx=9 {TAR_7Z} {TAR}')
        print(f'✅ 镜像: {TAR} ({os.path.getsize(TAR) / 1e6:.1f}MB), 压缩: {TAR_7Z} ({os.path.getsize(TAR_7Z) / 1e6:.1f}MB)', file=sys.stderr)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        print(f'🧹 已删除临时目录 {work}', file=sys.stderr)


if __name__ == '__main__':
    main()
