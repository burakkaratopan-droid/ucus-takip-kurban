"""IST <-> Japonya (Haneda, Narita, Kansai) Kurban Bayramı uçuş fiyat takipçisi.

Her çalıştırmada bayramı (arife dahil) tamamen kapsayan tüm gidiş-dönüş
kombinasyonlarını Google Flights üzerinden tarar ve sonuçları
docs/data.json dosyasına ekler. Panel (docs/index.html) bu dosyayı okur.
"""

import json
import os
import random
import re
import sys
import time
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fast_flights import FlightQuery, Passengers, create_query, fetch_flights_html
from selectolax.lexbor import LexborHTMLParser

ROOT = Path(__file__).parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
DATA_FILE = ROOT / "docs" / "data.json"


def destinations():
    """config.json'daki varış havalimanı kodları (eski tek 'destination' ayarı da desteklenir)."""
    if "destinations" in CONFIG:
        return [d["code"] for d in CONFIG["destinations"]]
    return [CONFIG["destination"]]


def combinations():
    """Bayramın ilk gününden (arife) önce/aynı gün gidip son gününden sonra/aynı gün dönen kombinasyonlar."""
    today = date.today()
    for h in CONFIG["holidays"]:
        first = date.fromisoformat(h["first_day"])
        last = date.fromisoformat(h["last_day"])
        for days in range(CONFIG["min_days"], CONFIG["max_days"] + 1):
            dep = last - timedelta(days=days)
            while dep <= first:
                ret = dep + timedelta(days=days)
                if dep > today:
                    for dest in destinations():
                        yield dest, h["id"], dep, ret, days
                dep += timedelta(days=1)


def build_query(dest, dep, ret, max_stops, safe=False):
    # "NRT-KIX" gibi kodlar açık çene rotasıdır: NRT'ye gidiş, KIX'ten dönüş (çoklu şehir araması).
    o = CONFIG["origin"]
    out_to, back_from = dest.split("-") if "-" in dest else (dest, dest)
    return create_query(
        flights=[
            FlightQuery(date=dep.isoformat(), from_airport=o, to_airport=out_to),
            FlightQuery(date=ret.isoformat(), from_airport=back_from, to_airport=o),
        ],
        trip="round-trip" if out_to == back_from else "multi-city",
        seat="economy",
        passengers=Passengers(adults=CONFIG["adults"]),
        language="tr",
        currency=CONFIG["currency"],
        max_stops=max_stops,
        # safe: 1 bavul ücreti dahil, ayrı biletli / kendi aktarmalı uçuşlar hariç
        checked_bags=1 if safe else 0,
        hide_separate_and_self_transfer=safe,
    )


def parse(html):
    """Google Flights sayfasından gidiş seçeneklerini ve gidiş-dönüş toplam fiyatlarını çıkarır.

    fast-flights'ın kendi ayrıştırıcısı fiyatsız sonuçlarda çöktüğü ve sadece
    "en iyi uçuşlar" bloğunu okuduğu için burada kendi ayrıştırıcımızı kullanıyoruz.
    """
    node = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if node is None:
        raise RuntimeError("Google Flights beklenen veriyi döndürmedi (engellenmiş olabilir)")
    js = node.text()
    raw = js.split("data:", 1)[1].rsplit(",", 1)[0]
    if raw.endswith("errorHasStatus: true"):
        return []
    payload = json.loads(raw)

    options = []
    for block in (2, 3):
        rows = payload[block][0] if len(payload) > block and payload[block] else None
        for row in rows or []:
            try:
                price = row[1][0][1]
            except (IndexError, TypeError):
                continue
            if not price:
                continue
            flight = row[0]
            legs = []
            for s in flight[2]:
                dep_t = (s[8] or []) + [None, None]
                arr_t = (s[10] or []) + [None, None]
                legs.append({
                    "from": s[3],
                    "to": s[6],
                    "dep": f"{dep_t[0] or 0:02d}:{dep_t[1] or 0:02d}",
                    "arr": f"{arr_t[0] or 0:02d}:{arr_t[1] or 0:02d}",
                    "arr_date": "-".join(f"{x:02d}" for x in s[21]) if s[21] else None,
                    "minutes": s[11],
                })
            options.append({
                "price": int(price),
                "airlines": flight[1],
                "legs": legs,
                "minutes": flight[9] if len(flight) > 9 and isinstance(flight[9], int) else None,
            })
    options.sort(key=lambda x: x["price"])
    return options


def fetch(dest, dep, ret, max_stops, safe=False, attempts=3):
    q = build_query(dest, dep, ret, max_stops, safe)
    err = None
    for i in range(attempts):
        try:
            options = parse(fetch_flights_html(q))
            # Google ara sıra boş sonuç döndürüyor; birkaç kez daha dene.
            if options or i == attempts - 1:
                return options, q.url()
        except Exception as e:  # ağ hatası, engelleme, değişen sayfa yapısı
            err = e
        time.sleep(5 * (i + 1) + random.random() * 3)
    raise err


_BROWSER = {}


def _page():
    if "page" not in _BROWSER:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()
        browser = pw.chromium.launch()
        _BROWSER.update(pw=pw, browser=browser,
                        page=browser.new_page(locale="tr-TR", viewport={"width": 1280, "height": 2000}))
    return _BROWSER["page"]


def close_browser():
    if "pw" in _BROWSER:
        _BROWSER["browser"].close()
        _BROWSER["pw"].stop()
        _BROWSER.clear()


_SP = r"[^\S\n]+"  # satır sonu hariç boşluk


def parse_result_text(text, origin, out_to):
    """Google Flights sonuç satırının görünen metninden uçuş seçeneği çıkarır (tarayıcı yolu)."""
    if "Business" in text or "Premium" in text:
        return None  # karışık kabinli pahalı seçenekler
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    price = re.search(r"₺" + r"[^\S\n]*([\d.]+)", text)
    times = re.search(r"(\d{1,2}:\d{2})\s*[–-]\s*(\d{1,2}:\d{2})", text)
    dur = re.search(r"(\d+)" + _SP + r"sa\.(?:" + _SP + r"(\d+)" + _SP + r"dk\.)?", text)
    if not (price and times and dur):
        return None
    if "Aktarmasız" in text:
        stops = 0
    else:
        m = re.search(r"(\d+)" + _SP + r"aktarma", text)
        if not m:
            return None
        stops = int(m.group(1))
    via = []
    if stops:
        m = re.search(r"(?:sa\.|dk\.)" + _SP + r"([A-Z]{3}(?:," + _SP + r"[A-Z]{3})*)\s*$", text[dur.end():], re.M)
        via = [v.strip() for v in m.group(1).split(",")] if m else ["?"] * stops
    # Havayolu adı, varış saatinin yazdığı satırdan sonraki satırdır.
    after = text[times.end():].split("\n", 2)
    airline_line = after[1].strip() if len(after) > 1 else ""
    airline_line = re.sub(r"([a-z])([A-Z])", r"\1, \2", airline_line.split("İşleten")[0])
    airlines = [a.strip() for a in re.split(r"[,·]", airline_line) if a.strip()] or ["?"]
    stopsl = [origin] + via + [out_to]
    legs = []
    for i in range(len(stopsl) - 1):
        legs.append({"from": stopsl[i], "to": stopsl[i + 1],
                     "dep": times.group(1) if i == 0 else "",
                     "arr": times.group(2) if i == len(stopsl) - 2 else "",
                     "arr_date": None, "minutes": None})
    return {
        "price": int(price.group(1).replace(".", "")),
        "airlines": airlines,
        "legs": legs,
        "minutes": int(dur.group(1)) * 60 + int(dur.group(2) or 0),
    }


def fetch_browser(dest, dep, ret, attempts=2):
    """Açık çene (çoklu şehir) sorgusunu tarayıcıda açar; sonuçlar tüm seyahatin fiyatıdır."""
    q = build_query(dest, dep, ret, 1)
    url = q.url()
    out_to = dest.split("-")[0]
    page = _page()
    for i in range(attempts):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_function(
                "() => [...document.querySelectorAll('li')].some(li => li.innerText.includes('₺'))",
                timeout=30000)
            more = page.get_by_role("button", name=re.compile("Daha fazla uçuş"))
            if more.count():
                more.first.click()
                page.wait_for_timeout(2500)
            texts = page.locator("li").all_inner_texts()
            _BROWSER["texts"] = texts
            options = [o for o in (parse_result_text(t, CONFIG["origin"], out_to) for t in texts if "₺" in t) if o]
            if options:
                options.sort(key=lambda x: x["price"])
                return options, url
        except Exception as e:  # zaman aşımı, sayfa yapısı değişikliği
            if i == attempts - 1:
                raise
            print(f"tarayıcı yeniden deneniyor ({dest}): {e}", file=sys.stderr)
        time.sleep(3)
    return [], url


def summarize(direct_opts, conn_opts):
    direct = next((o for o in direct_opts if len(o["legs"]) == 1), None)
    conn = [o for o in conn_opts if len(o["legs"]) == 2 and short_enough(o)]
    return direct, conn[:3]


def short_enough(o):
    """Gidiş yolculuk süresi (aktarma beklemesi dahil) sınırın altında mı? Süre bilinmiyorsa eler."""
    limit = CONFIG.get("max_connecting_hours")
    return not limit or (o.get("minutes") is not None and o["minutes"] < limit * 60)


def load_data():
    if not DATA_FILE.exists():
        return {"runs": [], "combos": {}}
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    # Eski kayıtlar sadece Haneda içindi ve anahtarlarında havalimanı yoktu: "HND_" önekiyle taşı.
    migrated = {}
    for key, c in data["combos"].items():
        if "dest" not in c:
            c["dest"] = "HND"
            key = f"HND_{key}"
        migrated[key] = c
    data["combos"] = migrated
    return data


def compact_option(o):
    if not o:
        return None
    return {
        "price": o["price"],
        "airlines": o["airlines"],
        "via": [leg["to"] for leg in o["legs"][:-1]],
        "legs": o["legs"],
        "minutes": o["minutes"],
    }


DIRECT_LOW_STEP = 1000  # aktarmasız "yeni en düşük" alarmı için gereken en az düşüş (TL)
PANEL_URL = "https://burakkaratopan-droid.github.io/ucus-takip-kurban/"


def last_value(entry, col):
    for p in reversed(entry["history"]):
        if len(p) > col and p[col] is not None:
            return p[col]
    return None


def tl(n):
    return f"{n:,}".replace(",", ".") + " TL"


def notify(alerts):
    """Alarm varsa GitHub'da bir issue açar; GitHub bunu depo sahibine e-postayla bildirir."""
    if not alerts:
        return
    title = "Fiyat alarmı: " + alerts[0]["title"] + (f" (+{len(alerts) - 1})" if len(alerts) > 1 else "")
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    owner = os.environ.get("GITHUB_REPOSITORY_OWNER")
    # Sahibi etiketleyip issue'yu ona atıyoruz: GitHub bu iki durumda izleme ayarından
    # bağımsız olarak e-posta gönderir.
    mention = f"@{owner} " if owner else ""
    body = (f"{mention}yeni fiyat alarmı:\n\n" + "\n".join(f"- {a['text']}" for a in alerts)
            + f"\n\n[Paneli aç]({PANEL_URL})")
    if not (token and repo):
        print("ALARM (GitHub dışında, issue açılmadı):\n" + title + "\n" + body)
        return
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues",
        data=json.dumps({"title": title, "body": body, "assignees": [owner] if owner else []}).encode(),
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("Alarm issue açıldı:", json.load(r).get("html_url"))
    except Exception as e:  # bildirim hatası taramayı bozmasın
        print("Alarm issue açılamadı:", e, file=sys.stderr)


RETURN_CHECKS = 25  # her taramada dönüş süresi doğrulanacak en ucuz aktarmalı kombinasyon sayısı
RETURN_QUERY_CAP = 60  # bunun için yapılabilecek en fazla sorgu


def fetch_return_oneway(dest, ret):
    """Dönüş yönünü tek yön olarak sorgular; sadece süreleri öğrenmek için (fiyatı kullanılmaz)."""
    back_from = dest.split("-")[1] if "-" in dest else dest
    q = create_query(
        flights=[FlightQuery(date=ret.isoformat(), from_airport=back_from, to_airport=CONFIG["origin"])],
        trip="one-way", seat="economy", passengers=Passengers(adults=CONFIG["adults"]),
        language="tr", currency=CONFIG["currency"], max_stops=1,
    )
    for i in range(3):
        try:
            opts = parse(fetch_flights_html(q))
            if opts:
                return opts
        except Exception as e:
            print(f"dönüş sorgusu hata ({dest} {ret}): {e}", file=sys.stderr)
        time.sleep(4 * (i + 1))
    return []


def set_conn_point(entry, run_idx):
    """Aktarmalı listesi değiştiyse bu taramanın geçmiş noktasını güncelle."""
    price = entry["connecting"][0]["price"] if entry.get("connecting") else None
    h = entry["history"]
    if h and h[-1][0] == run_idx:
        h[-1][2] = price
    elif not h or h[-1][2] != price:
        h.append([run_idx, entry["direct"]["price"] if entry.get("direct") else None, price])


def check_returns(data, run_idx):
    """En ucuz aktarmalılarda aynı havayolunun dönüşü de sınırın altında mı? Değilse o seçeneği ele."""
    limit = (CONFIG.get("max_connecting_hours") or 999) * 60
    verified = queries = 0
    while verified < RETURN_CHECKS and queries < RETURN_QUERY_CAP:
        todo = sorted(
            ((k, c) for k, c in data["combos"].items()
             if c.get("checked") == run_idx and c.get("connecting") and not c["stale"].get("connecting")
             and c.get("ret_checked") != run_idx),
            key=lambda kc: kc[1]["connecting"][0]["price"])
        if not todo:
            break
        key, entry = todo[0]
        time.sleep(1 + random.random() * 2)
        returns = [o for o in fetch_return_oneway(entry["dest"], date.fromisoformat(entry["ret"]))
                   if len(o["legs"]) <= 2 and o.get("minutes")]
        queries += 1
        entry["ret_checked"] = run_idx
        if not returns:
            print(f"dönüş {key}: sonuç yok, kontrol edilemedi")
            continue
        kept = []
        for o in entry["connecting"]:
            same = [r["minutes"] for r in returns if set(r["airlines"]) & set(o["airlines"][:1])]
            if same and min(same) < limit:
                o["ret_minutes"] = min(same)
                kept.append(o)
        before = entry["connecting"][0]["price"]
        if kept:
            entry["connecting"] = kept
        else:
            del entry["connecting"]
        set_conn_point(entry, run_idx)
        after = entry["connecting"][0]["price"] if entry.get("connecting") else None
        if kept:
            verified += 1
        print(f"dönüş {key}: {before} → {after}")


def main():
    data = load_data()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data["runs"].append(now)
    run_idx = len(data["runs"]) - 1
    combos = list(combinations())
    threshold = CONFIG["connecting_threshold"]
    ok = failed = 0

    # Alarm karşılaştırması için tarama öncesi durum
    prev_direct_low = min((p[1] for c in data["combos"].values() for p in c["history"] if p[1] is not None),
                          default=None)

    for n, (dest, hid, dep, ret, days) in enumerate(combos, 1):
        key = f"{dest}_{dep.isoformat()}_{ret.isoformat()}"
        try:
            if "-" in dest:
                # Açık çene: Google bu sonuçları sayfaya sonradan yüklüyor, gerçek tarayıcıyla okunuyor.
                opts, conn_url = fetch_browser(dest, dep, ret)
                direct_opts, conn_opts, direct_url = opts, opts, conn_url
            else:
                direct_opts, direct_url = fetch(dest, dep, ret, 0)
                time.sleep(1 + random.random() * 2)
                conn_opts, conn_url = fetch(dest, dep, ret, 1)
            time.sleep(1 + random.random() * 2)
        except Exception as e:
            failed += 1
            print(f"[{n}/{len(combos)}] {key} HATA: {e}", file=sys.stderr)
            continue

        direct, conn = summarize(direct_opts, conn_opts)
        entry = data["combos"].setdefault(key, {
            "dest": dest, "holiday": hid, "dep": dep.isoformat(), "ret": ret.isoformat(),
            "days": days, "history": [],
        })
        # Google bazen boş döndürüyor: o zaman son bilinen fiyatı koru, "eski" diye işaretle.
        stale = {}
        if direct:
            entry["direct"] = compact_option(direct)
        elif entry.get("direct"):
            stale["direct"] = True
        if conn:
            entry["connecting"] = [compact_option(c) for c in conn]
        elif entry.get("connecting"):
            stale["connecting"] = True
        entry["stale"] = stale

        point = [run_idx,
                 entry["direct"]["price"] if entry.get("direct") else None,
                 entry["connecting"][0]["price"] if entry.get("connecting") else None]
        # Sadece fiyat değiştiğinde yeni nokta ekle; dosya küçük kalsın.
        if not entry["history"] or entry["history"][-1][1:3] != point[1:]:
            entry["history"].append(point)
        entry["checked"] = run_idx
        entry["urls"] = {"direct": direct_url, "connecting": conn_url}
        ok += 1
        print(f"[{n}/{len(combos)}] {key} aktarmasız={point[1]} aktarmalı={point[2]}"
              + (f" (eski: {', '.join(stale)})" if stale else ""))

    check_returns(data, run_idx)

    # Alarmlar
    alerts = []
    for key, entry in data["combos"].items():
        if entry.get("checked") != run_idx or entry["stale"].get("connecting") or not entry.get("connecting"):
            continue
        price, alerted = entry["connecting"][0]["price"], entry.get("alerted_conn")
        # Aynı kombinasyon için ancak daha önce bildirilenden daha ucuzsa tekrar haber ver.
        if price <= threshold and (alerted is None or price < alerted):
            entry["alerted_conn"] = price
            o = entry["connecting"][0]
            alerts.append({
                "title": f"{tl(price)} aktarmalı ({entry['dest']})",
                "text": f"IST ⇄ {entry['dest']} {entry['dep']} → {entry['ret']} ({entry['days']} gün): {tl(price)}, "
                        f"{', '.join(o['airlines'])} · {', '.join(o['via'])} aktarmalı."
                        f" [Google Flights]({entry['urls']['connecting']})",
            })
    lows = [(c["direct"]["price"], c) for c in data["combos"].values()
            if c.get("checked") == run_idx and c.get("direct") and not c["stale"].get("direct")]
    if lows and prev_direct_low is not None:
        price, c = min(lows, key=lambda x: x[0])
        if price <= prev_direct_low - DIRECT_LOW_STEP:
            alerts.append({
                "title": f"aktarmasız yeni en düşük {tl(price)} ({c['dest']})",
                "text": f"Aktarmasız şimdiye kadarki en düşük fiyat: IST ⇄ {c['dest']} {c['dep']} → {c['ret']} ({c['days']} gün) "
                        f"{tl(price)}, {', '.join(c['direct']['airlines'])} (önceki en düşük {tl(prev_direct_low)}). "
                        f"[Google Flights]({c['urls']['direct']})",
            })
    notify(alerts)

    close_browser()
    data["updated"] = now
    data["config"] = CONFIG
    data["last_run"] = {
        "at": now, "ok": ok, "failed": failed, "total": len(combos),
        "stale": sum(1 for c in data["combos"].values() if c.get("checked") == run_idx and c["stale"]),
    }
    DATA_FILE.parent.mkdir(exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Bitti: {ok} başarılı, {failed} hatalı / {len(combos)}, {len(alerts)} alarm")
    # Hepsi başarısızsa iş akışı kırmızı görünsün.
    sys.exit(1 if ok == 0 and combos else 0)


if __name__ == "__main__":
    if "--test-alarm" in sys.argv:
        notify([{"title": "test", "text": "Bu bir test alarmıdır. Bu e-postayı aldıysan bildirimler çalışıyor."}])
    else:
        main()
