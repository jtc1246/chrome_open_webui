#!/usr/bin/env python3
# 多线程分段下载: 先跟随重定向拿到最终地址和总长度, 按固定分片 Range 并发下载, 失败分片重试, 最后按序合并
import sys, os, threading, urllib.request, queue

CHUNK = 1 << 20
THREADS = 32
RETRY = 5
UA = 'curl/8.5.0'  # 部分站点拒绝 urllib 默认的 User-Agent


def resolve(url):
    req = urllib.request.Request(url, headers={'Range': 'bytes=0-0', 'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        total = int(r.headers['Content-Range'].split('/')[-1])  # 不支持 Range 的服务器这里会直接报错
        return r.geturl(), total


def fetch(url, start, end):
    req = urllib.request.Request(url, headers={'Range': f'bytes={start}-{end}', 'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    if len(data) != end - start + 1:
        raise IOError(f'分片长度不符 {start}-{end}: {len(data)}')
    return data


def main(url, out):
    final, total = resolve(url)
    print(f'ℹ️ {os.path.basename(out)}: {total / 1e6:.1f}MB, {(total + CHUNK - 1) // CHUNK} 片', file=sys.stderr)
    parts = {}
    q = queue.Queue()
    for s in range(0, total, CHUNK):
        q.put((s, min(s + CHUNK, total) - 1))
    errors = []
    def worker():
        while True:
            try:
                s, e = q.get_nowait()
            except queue.Empty:
                return
            for i in range(RETRY):
                try:
                    parts[s] = fetch(final, s, e)
                    break
                except Exception as ex:
                    if i == RETRY - 1:
                        errors.append(f'{s}-{e}: {ex}')
    ts = [threading.Thread(target=worker) for _ in range(THREADS)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    if errors:
        print('❌ 下载失败的分片:\n' + '\n'.join(errors), file=sys.stderr)
        os._exit(1)
    with open(out, 'wb') as f:
        for s in sorted(parts):
            f.write(parts[s])
    print(f'✅ {out} ({os.path.getsize(out)} 字节)', file=sys.stderr)


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
