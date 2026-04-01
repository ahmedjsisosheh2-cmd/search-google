import sys
sys.dont_write_bytecode = True
import requests
import urllib.parse
import re
import os
import time
import random
import gc as gcol
import threading
import asyncio
import aiohttp
from aiohttp_socks import ProxyConnector
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue, Empty
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from stem import Signal
from stem.control import Controller

out = "google-links.txt"

flock = threading.Lock()
tlock = threading.Lock()
slock = threading.Lock()

seen = set()
links = []
sess = []
stop = threading.Event()

thost = '127.0.0.1'
tsport = 9150
tcport = 9151
tpass = ''

tprox = {
    'http': f'socks5h://{thost}:{tsport}',
    'https': f'socks5h://{thost}:{tsport}'
}

ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0'
langs = ["en-US,en;q=0.9", "en-GB,en;q=0.8", "en-US,en;q=0.5", "en-CA,en;q=0.7"]
plats = ["Win32", "Win64", "Windows"]
views = ["1920", "1366", "1536", "1440", "1280", "1600"]

pool = Queue()
pmin = 100
puse = 10

mode = None
mprox = []
mlock = threading.Lock()


def header(search=False):
    if search:
        return {
            'User-Agent': 'Nokia6230/2.0 (04.44) Profile/MIDP-2.0 Configuration/CLDC-1.1',
            'Accept': 'text/html,text/vnd.wap.wml,application/xhtml+xml',
            'Accept-Language': 'en',
        }
    return {
        'User-Agent': ua,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': random.choice(langs),
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Sec-GPC': '1',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Sec-CH-UA-Platform': f'"{random.choice(plats)}"',
        'Sec-CH-UA-Mobile': '?0',
        'Viewport-Width': random.choice(views),
        'DNT': str(random.randint(0, 1)),
        'Pragma': 'no-cache',
        'Cache-Control': 'no-cache',
    }


def renew():
    with tlock:
        try:
            with Controller.from_port(port=tcport) as ctrl:
                ctrl.authenticate(password=tpass)
                ctrl.signal(Signal.NEWNYM)
            return True
        except Exception:
            return False


def nproxy():
    with mlock:
        if not mprox:
            return None
        return random.choice(mprox)


def chkprox(proxy):
    try:
        proxy.split(':')[1]
    except (IndexError, ValueError):
        return None
    for scheme in ['http', 'https', 'socks5', 'socks4']:
        px = proxy if scheme in ('http', 'https') else f'{scheme}://{proxy}'
        try:
            r = requests.get(
                'http://www.google.com',
                proxies={'http': px, 'https': px},
                timeout=5
            )
            if r.status_code == 200:
                return {'proxy': proxy, 'px': px}
        except Exception:
            pass
    return None


def loadprox(path):
    global mprox
    if not os.path.isfile(path):
        print("error")
        return 0

    with open(path, 'r') as f:
        raw = [l.strip() for l in f if l.strip()]

    valid = []
    cnt = [0]
    lk = threading.Lock()

    def worker(proxy):
        res = chkprox(proxy)
        if res:
            with lk:
                valid.append(res)
                cnt[0] += 1
                print(f"\rproxy : {cnt[0]}", end='', flush=True)

    with ThreadPoolExecutor(max_workers=100) as ex:
        futs = [ex.submit(worker, p) for p in raw]
        for f in as_completed(futs):
            pass

    print()
    mprox = valid
    return len(valid)


def mksess():
    s = requests.Session()
    a = HTTPAdapter(pool_connections=20, pool_maxsize=100,
                    max_retries=Retry(total=0), pool_block=False)
    s.mount('http://', a)
    s.mount('https://', a)
    with slock:
        sess.append(s)
    return s


def setcook(s):
    hd = header(search=False)
    try:
        s.get('https://www.google.com/', headers=hd, proxies=tprox, timeout=(8, 15))
        if 'SOCS' not in s.cookies:
            s.post('https://consent.google.com/save',
                   data={'gl': 'US', 'pc': 'srp', 'continue': 'https://www.google.com/',
                         'set_eom': 'true', 'bl': 'boq_identityfrontenduiserver_20250101.00_p0',
                         'hl': 'en', 'src': '1', 'act': '1'},
                   headers={**hd, 'Referer': 'https://www.google.com/'},
                   proxies=tprox, timeout=(8, 15))
        return len(s.cookies) > 0
    except Exception:
        return False


def addsess():
    try:
        s = mksess()
        if setcook(s):
            pool.put({'session': s, 'uses': 0})
            return True
        else:
            with slock:
                if s in sess:
                    sess.remove(s)
            s.close()
            return False
    except Exception:
        return False


def fillpool(count=100):
    with ThreadPoolExecutor(max_workers=200) as ex:
        att = 0
        while pool.qsize() < count and att < count * 3:
            rem = count - pool.qsize()
            batch = min(rem + 10, 200)
            futs = [ex.submit(addsess) for _ in range(batch)]
            for f in as_completed(futs):
                pass
            att += batch
            if pool.qsize() >= count:
                break
    return pool.qsize()


def refill():
    while not stop.is_set():
        if pool.qsize() < pmin:
            need = (pmin - pool.qsize()) + 20
            with ThreadPoolExecutor(max_workers=200) as ex:
                futs = [ex.submit(addsess) for _ in range(need)]
                for f in as_completed(futs):
                    if pool.qsize() >= pmin:
                        break
        time.sleep(0.5)


def islast(html):
    low = html.lower()
    return any(s in low for s in [
        'did not match any documents', 'no results found',
        'id="ofr"', 'no more results'
    ])


def extract(html):
    urls = []
    su = set()
    for m in re.finditer(r'/url\?q=(https?://[^&"]+)', html):
        u = urllib.parse.unquote(m.group(1))
        if 'google.com' not in u and u not in su:
            su.add(u)
            urls.append(u)
    for m in re.finditer(
        r'<a[^>]+href="(https?://(?!google\.com|gstatic|youtube\.com/s)[^"]+)"[^>]*>(.*?)</a>',
        html, re.DOTALL
    ):
        u = m.group(1)
        t = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        if u not in su and t and len(t) > 5:
            su.add(u)
            urls.append(u)
    return urls


async def mkaio():
    conn = ProxyConnector.from_url(
        f'socks5://{thost}:{tsport}', rdns=True)
    jar = aiohttp.CookieJar(unsafe=True)
    s = aiohttp.ClientSession(connector=conn, cookie_jar=jar)
    hd = header(search=False)
    try:
        async with s.get('https://www.google.com/', headers=hd,
                         timeout=aiohttp.ClientTimeout(total=15)) as r:
            await r.text()
        if not any(c.key == 'SOCS' for c in jar):
            fd = aiohttp.FormData()
            for k, v in [('gl', 'US'), ('pc', 'srp'), ('continue', 'https://www.google.com/'),
                         ('set_eom', 'true'), ('bl', 'boq_identityfrontenduiserver_20250101.00_p0'),
                         ('hl', 'en'), ('src', '1'), ('act', '1')]:
                fd.add_field(k, v)
            async with s.post('https://consent.google.com/save', data=fd,
                              headers={**hd, 'Referer': 'https://www.google.com/'},
                              timeout=aiohttp.ClientTimeout(total=15)) as r:
                await r.text()
    except Exception:
        pass
    return s


async def fetchtor(pg, query, aio):
    start = (pg - 1) * 10
    params = {'q': query, 'hl': 'en', 'gl': 'us', 'num': '10',
              'start': str(start), 'gbv': '1', 'ie': 'ISO-8859-1', 'safe': 'off'}
    hd = header(search=True)

    for retry in range(3):
        try:
            async with aio.get(
                'https://www.google.com/search', params=params, headers=hd,
                timeout=aiohttp.ClientTimeout(total=20, connect=8), allow_redirects=True
            ) as resp:
                if resp.status == 429:
                    renew()
                    continue
                if resp.status != 200:
                    continue
                txt = await resp.text()
                low = txt.lower()
                if 'captcha' in low or 'unusual traffic' in low:
                    renew()
                    continue
                if len(txt) < 1000:
                    renew()
                    continue
                if islast(txt):
                    return pg, [], "no_results"
                return pg, extract(txt), "ok"
        except asyncio.TimeoutError:
            pass
        except Exception:
            if retry < 2:
                renew()
    return pg, None, "failed"


def fetchmob(pg, query):
    start = (pg - 1) * 10
    params = {'q': query, 'hl': 'en', 'gl': 'us', 'num': '10',
              'start': str(start), 'gbv': '1', 'ie': 'ISO-8859-1', 'safe': 'off'}
    hd = header(search=True)

    for retry in range(5):
        pi = nproxy()
        if not pi:
            return pg, None, "no_proxy"
        px = {'http': pi['px'], 'https': pi['px']}
        try:
            r = requests.get(
                'https://www.google.com/search',
                params=params, headers=hd, proxies=px,
                timeout=8, allow_redirects=True
            )
            if r.status_code == 429:
                continue
            if r.status_code != 200:
                continue
            low = r.text.lower()
            if 'captcha' in low or 'unusual traffic' in low:
                continue
            if len(r.text) < 1000:
                continue
            if islast(r.text):
                return pg, [], "no_results"
            return pg, extract(r.text), "ok"
        except Exception:
            pass
    return pg, None, "failed"


async def runtor(kw, pages, fh):
    kwseen = set()
    total = 0

    batch = 10
    for bs in range(1, pages + 1, batch):
        be = min(bs + batch, pages + 1)
        pgs = list(range(bs, be))

        aio = await mkaio()

        tasks = []
        for pg in pgs:
            tasks.append(fetchtor(pg, kw, aio))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        sres = []
        for res in results:
            if not isinstance(res, Exception):
                sres.append(res)
        sres.sort(key=lambda x: x[0])

        end = False

        for res in sres:
            pg, urls, status = res
            if status == "ok" and urls is not None:
                with flock:
                    for u in urls:
                        if u not in seen and u not in kwseen:
                            seen.add(u)
                            kwseen.add(u)
                            links.append(u)
                            fh.write(u + '\n')
                            fh.flush()
                            total += 1
                print(f"page : {pg:<4}  |  links : {total}")
            elif status == "no_results":
                end = True

        try:
            await aio.close()
        except Exception:
            pass

        if end:
            break

    return total


def runmob(kw, pages, fh):
    kwseen = set()
    total = 0
    failed = []

    with ThreadPoolExecutor(max_workers=100) as ex:
        batch = 10
        for bs in range(1, pages + 1, batch):
            be = min(bs + batch, pages + 1)
            pgs = list(range(bs, be))
            futs = {ex.submit(fetchmob, pg, kw): pg for pg in pgs}
            end = False

            bres = []
            for fut in as_completed(futs):
                try:
                    res = fut.result()
                    bres.append(res)
                except Exception:
                    continue

            bres.sort(key=lambda x: x[0])

            for pg, urls, status in bres:
                if status == "ok" and urls is not None:
                    with flock:
                        for u in urls:
                            if u not in seen and u not in kwseen:
                                seen.add(u)
                                kwseen.add(u)
                                links.append(u)
                                fh.write(u + '\n')
                                fh.flush()
                                total += 1
                    print(f"page : {pg:<4}  |  links : {total}")
                elif status == "no_results":
                    end = True
                elif status == "failed":
                    failed.append(pg)

            if end:
                break

        if failed:
            futs = {ex.submit(fetchmob, pg, kw): pg for pg in failed}
            bres = []
            for fut in as_completed(futs):
                try:
                    res = fut.result()
                    bres.append(res)
                except Exception:
                    continue

            bres.sort(key=lambda x: x[0])

            for pg, urls, status in bres:
                if status == "ok" and urls is not None:
                    with flock:
                        for u in urls:
                            if u not in seen and u not in kwseen:
                                seen.add(u)
                                kwseen.add(u)
                                links.append(u)
                                fh.write(u + '\n')
                                fh.flush()
                                total += 1
                    print(f"page : {pg:<4}  |  links : {total}")

    return total


def cleanup():
    with slock:
        for s in sess:
            try:
                s.close()
            except Exception:
                pass
        sess.clear()
    while not pool.empty():
        try:
            pool.get_nowait()['session'].close()
        except Exception:
            break
    gcol.collect()
    if mode == 'windows':
        try:
            with Controller.from_port(port=tcport) as ctrl:
                ctrl.authenticate(password=tpass)
                for circ in ctrl.get_circuits():
                    try:
                        ctrl.close_circuit(circ.id)
                    except Exception:
                        pass
                ctrl.signal(Signal.NEWNYM)
        except Exception:
            pass


def banner():
    return (
        "generated by vipqvip\n"
        "source google search results\n"
        "telegram @vipqvip\n"
        "channel @python_vipqvip\n"
        "===========================================================================================================\n"
    )


def main():
    global mode

    print("  1. windows")
    print("  2. mobile")
    ch = input("choice -1- or -2- : ").strip()

    if ch == '1':
        mode = 'windows'
    elif ch == '2':
        mode = 'mobile'
    else:
        return

    if mode == 'mobile':
        pfile = input("proxy file: ").strip()
        n = loadprox(pfile)
        if n == 0:
            print("no proxies found")
            return
        print(f"proxy : {n}")

    if mode == 'windows':
        fillpool(100)
        rt = threading.Thread(target=refill, daemon=True)
        rt.start()

    with open(out, 'w', encoding='utf-8') as fh:
        fh.write(banner())
        fh.flush()

        while True:
            kw = input("keyword: ").strip()
            if not kw:
                break
            try:
                npg = int(input("pages: ").strip())
            except ValueError:
                print("error")
                continue

            print()

            if mode == 'windows':
                total = asyncio.run(runtor(kw, npg, fh))
            else:
                total = runmob(kw, npg, fh)

            print(f"done : {total}")

    if mode == 'windows':
        stop.set()

    print(f"total links: {len(links)}")
    print(f"saved : {out}")
    cleanup()


if __name__ == "__main__":
    main()