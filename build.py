#!/usr/bin/env python3
# 输入 ChromePublic.apk, 输出改好的 apk: 解开编译环境镜像 (image/chrome_edit_env.tar, 没有就从 .7z 解压) 到临时目录,
# 把核心转换代码 (patch/) 和配置 (config/) 拷进去, chroot 执行转换, 取回结果后删除临时目录。代码和配置都不在镜像里
# 用法: python3 -u build.py <输入apk> <输出apk> [--tmp 临时目录基址, 默认 /dev/shm]
import os, sys, shutil, tempfile, argparse, subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, 'image'))
from make_image import TAR, TAR_7Z, CHROOT_ENV, ensure_7zz


def fail(msg):
    print(f'❌ {msg}', file=sys.stderr)
    raise SystemExit(1)  # 不用 os._exit: 要让 finally 删掉临时目录


def run(cmd):
    print(f'ℹ️ {cmd}', file=sys.stderr)
    r = subprocess.run(cmd, shell=True)
    if r.returncode != 0:
        fail(f'命令失败 ({r.returncode}): {cmd}')


def main():
    ap = argparse.ArgumentParser(description='ChromePublic.apk 注入固定命令行参数、切断无障碍入口并重新签名')
    ap.add_argument('src', help='输入 apk')
    ap.add_argument('out', help='输出 apk')
    ap.add_argument('--tmp', default='/dev/shm', help='临时目录基址, 其下创建随机子目录, 结束后删除')
    args = ap.parse_args()
    src, out = os.path.abspath(args.src), os.path.abspath(args.out)
    if not os.path.isfile(src):
        fail(f'输入 apk 不存在: {src}')
    if not os.path.exists(TAR):
        if not os.path.exists(TAR_7Z):
            fail(f'镜像 {TAR} 和 {TAR_7Z} 都不存在, 先运行 python3 -u image/make_image.py')
        run(f'{ensure_7zz()} x -o{os.path.dirname(TAR)} {TAR_7Z}')
    cfg_dir = os.path.join(ROOT, 'config')
    keystore_existed = os.path.exists(os.path.join(cfg_dir, 'keystore.jks'))
    work = tempfile.mkdtemp(prefix='chrome_edit_', dir=args.tmp)
    rootfs = os.path.join(work, 'rootfs')
    try:
        os.makedirs(rootfs)
        run(f'tar -xpf {TAR} --numeric-owner -C {rootfs}')
        os.makedirs(f'{rootfs}/work')
        shutil.copy(os.path.join(ROOT, 'patch/patch_apk.py'), f'{rootfs}/work/patch_apk.py')
        shutil.copytree(cfg_dir, f'{rootfs}/work/config')
        shutil.copy(src, f'{rootfs}/work/in.apk')
        run(f'chroot {rootfs} {CHROOT_ENV} python3 -u /work/patch_apk.py /work/in.apk /work/out.apk /work/config /work/tmp')
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copy(f'{rootfs}/work/out.apk', out)
        if not keystore_existed:  # 第一次构建时生成的签名密钥拷回配置目录, 以后一直用它签名
            shutil.copy(f'{rootfs}/work/config/keystore.jks', os.path.join(cfg_dir, 'keystore.jks'))
            print(f'⚠️ 已生成新的签名密钥 {cfg_dir}/keystore.jks, 务必备份: 丢失后新版本无法覆盖安装', file=sys.stderr)
        print(f'✅ 输出: {out} ({os.path.getsize(out) / 1e6:.1f}MB)', file=sys.stderr)
    finally:
        shutil.rmtree(work, ignore_errors=True)
        print(f'🧹 已删除临时目录 {work}', file=sys.stderr)


if __name__ == '__main__':
    main()
