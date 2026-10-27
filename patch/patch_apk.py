#!/usr/bin/env python3
# 核心转换 (在镜像的 chroot 里运行, 工具在镜像的 /opt 下): 输入 ChromePublic.apk, 输出改好并重新签名的 apk
# 1. 命令行参数写死: Application.attachBaseContext 里调用 CommandLine.init(args) 之前, 把 args 换成配置里的参数数组
#    (原本是从 /data/local/tmp/chrome-command-line 读出来的, 读不到时为 null)
# 2. 无障碍入口切断: 安卓系统 (以及开着自动填充服务时 Chrome 自己) 通过 WebContentsAccessibilityImpl.getAccessibilityNodeProvider
#    (混淆名 q) 拿网页的无障碍接口, 第一次调用时创建 native 端的无障碍对象、网页开始维护无障碍树。一进来就返回 null
# 用法: python3 -u patch_apk.py <输入apk> <输出apk> <配置目录> <临时工作目录>
import os, sys, json, glob, shutil, zipfile, subprocess

JAVA = '/opt/jre/bin/java'
KEYTOOL = '/opt/jre/bin/keytool'
SMALI = '/opt/smali/smali.jar'
BAKSMALI = '/opt/smali/baksmali.jar'
ZIPALIGN = '/opt/build-tools/zipalign'
APKSIGNER = '/opt/build-tools/lib/apksigner.jar'
# 注入点: 读完参数文件后汇合到这个 label, v0 是参数数组或 null; v4 在这里是空闲寄存器, 后面马上被重新赋值
ANCHOR = '    :goto_22f\n    invoke-static {v0}, Lorg/chromium/base/CommandLine;->e([Ljava/lang/String;)V\n'
AX_FILE = 'smali2/org/chromium/content/browser/accessibility/WebContentsAccessibilityImpl.smali'
AX_ANCHOR = '.method public final q()Landroid/view/accessibility/AccessibilityNodeProvider;\n    .registers 19\n'


def fail(msg):
    print(f'❌ {msg}', file=sys.stderr)
    os._exit(1)


def run(cmd):
    print(f'ℹ️ {cmd}', file=sys.stderr)
    r = subprocess.run(cmd, shell=True)
    if r.returncode != 0:
        fail(f'命令失败 ({r.returncode}): {cmd}')


def build_args(cfg, cfg_dir):
    v = json.load(open(os.path.join(cfg_dir, cfg['variations']), encoding='utf-8'))
    args = ['chrome'] + [f'--{k}={v[k]}' for k in ('enable-features', 'disable-features', 'force-fieldtrials', 'force-fieldtrial-params')] + cfg['extra_switches']
    joined = '\n'.join(args)
    bad = {c for c in joined if c in '"\\' or ord(c) > 126}
    if bad:
        fail(f'参数里有需要转义的字符, 未处理: {bad}')
    print(f'ℹ️ 参数 {len(args)} 项, 共 {len(joined)} 字符: {[a[:60] for a in args]}', file=sys.stderr)
    return joined


def patch_smali(work, joined):
    hits = [f for f in glob.glob(f'{work}/smali/**/*.smali', recursive=True) if ANCHOR in open(f, encoding='utf-8').read()]
    if len(hits) != 1:
        fail(f'注入点应恰好出现在 1 个文件里, 实际: {hits}')
    s = open(hits[0], encoding='utf-8').read()
    if s.count(ANCHOR) != 1 or 'chrome-command-line' not in s:
        fail(f'{hits[0]} 里注入点出现 {s.count(ANCHOR)} 次, 或不是读参数文件的那个类')
    inject = ('    :goto_22f\n'
              f'    const-string v0, "{joined.replace(chr(10), chr(92) + "n")}"\n'
              '    const-string v4, "\\n"\n'
              '    invoke-virtual {v0, v4}, Ljava/lang/String;->split(Ljava/lang/String;)[Ljava/lang/String;\n'
              '    move-result-object v0\n'
              '    invoke-static {v0}, Lorg/chromium/base/CommandLine;->e([Ljava/lang/String;)V\n')
    open(hits[0], 'w', encoding='utf-8').write(s.replace(ANCHOR, inject))
    print(f'✅ 已注入命令行参数 {hits[0]}', file=sys.stderr)
    ax = f'{work}/{AX_FILE}'
    s = open(ax, encoding='utf-8').read()
    if s.count(AX_ANCHOR) != 1:
        fail(f'{ax} 里无障碍入口出现 {s.count(AX_ANCHOR)} 次, 应为 1')
    open(ax, 'w', encoding='utf-8').write(s.replace(AX_ANCHOR, AX_ANCHOR + '    const/4 v0, 0x0\n    return-object v0\n'))
    print(f'✅ 已切断无障碍入口 {ax}', file=sys.stderr)


def repack(src_apk, out_apk, new_dex, work, cfg, cfg_dir):
    # 除被替换的 dex 和旧签名外的条目原样复制, 保持各自的压缩方式 (native 库必须保持不压缩)
    zin = zipfile.ZipFile(src_apk)
    with zipfile.ZipFile(f'{work}/unsigned.apk', 'w') as zout:
        for info in zin.infolist():
            n = info.filename
            if n.startswith('META-INF/') and n.rsplit('.', 1)[-1] in ('SF', 'RSA', 'EC', 'DSA', 'MF'):
                continue
            data = open(new_dex[n], 'rb').read() if n in new_dex else zin.read(n)
            zi = zipfile.ZipInfo(n, date_time=info.date_time)
            zi.compress_type = info.compress_type
            zi.external_attr = info.external_attr
            zout.writestr(zi, data)
    run(f'{ZIPALIGN} -f -P 16 4 {work}/unsigned.apk {work}/aligned.apk')
    ks = os.path.join(cfg_dir, cfg['keystore'])
    pw = cfg['keystore_pass']
    if not os.path.exists(ks):  # 第一次构建生成签名密钥; 之后一直用同一个, 新版本才能覆盖安装
        run(f'{KEYTOOL} -genkeypair -keystore {ks} -storepass {pw} -keypass {pw} -alias {cfg["key_alias"]} '
            f'-keyalg RSA -keysize 2048 -validity 36500 -dname "CN={cfg["key_alias"]}"')
    run(f'{JAVA} -jar {APKSIGNER} sign --ks {ks} --ks-pass pass:{pw} --ks-key-alias {cfg["key_alias"]} --out {out_apk} {work}/aligned.apk')
    run(f'{JAVA} -jar {APKSIGNER} verify --print-certs {out_apk}')


def main():
    src_apk, out_apk, cfg_dir, work = sys.argv[1:5]
    cfg = json.load(open(os.path.join(cfg_dir, 'config.json'), encoding='utf-8'))
    joined = build_args(cfg, cfg_dir)
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    z = zipfile.ZipFile(src_apk)
    z.extract('classes.dex', work)
    z.extract('classes2.dex', work)
    run(f'{JAVA} -Xmx4g -jar {BAKSMALI} d -a 28 {work}/classes.dex -o {work}/smali')
    run(f'{JAVA} -Xmx4g -jar {BAKSMALI} d -a 28 {work}/classes2.dex -o {work}/smali2')
    patch_smali(work, joined)
    run(f'{JAVA} -Xmx4g -Xss16m -jar {SMALI} a -a 28 {work}/smali -o {work}/classes_new.dex')
    run(f'{JAVA} -Xmx4g -Xss16m -jar {SMALI} a -a 28 {work}/smali2 -o {work}/classes2_new.dex')
    repack(src_apk, out_apk, {'classes.dex': f'{work}/classes_new.dex', 'classes2.dex': f'{work}/classes2_new.dex'}, work, cfg, cfg_dir)
    print(f'✅ 完成: {out_apk} ({os.path.getsize(out_apk) / 1e6:.1f}MB)', file=sys.stderr)


if __name__ == '__main__':
    main()
