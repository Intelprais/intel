#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
page_harvest.py — «ручной» сбор: вы сами листаете сайт в своём Chrome,
скрипт только считывает уже открытую страницу.

Зачем: Авито и Ярмарка Мастеров показывают CAPTCHA автоматической навигации.
Здесь навигации нет — страницы открываете и листаете вы, как обычный человек.
Скрипт подключается к вашей вкладке и по команде забирает то, что на ней видно.
CAPTCHA не обходится и не трогается: если она появилась, вы решаете её сами, как обычно.

Работает с Авито, Ярмаркой Мастеров, Яндекс Услугами, 2ГИС и любым сайтом со
списком карточек — разбор универсальный, по ссылкам.

Запуск:
  1) Chrome уже открыт с --remote-debugging-port=9222
  2) python page_harvest.py --out avito_fursuit
  3) Откройте нужную выдачу в браузере, промотайте вниз до конца
  4) Вернитесь в консоль, нажмите Enter — страница считается
  5) Листайте на следующую страницу, снова Enter. И так сколько нужно
  6) Введите q + Enter — сохранить и выйти

Всё собранное копится в один файл, дубли по ссылке отсекаются автоматически.
"""
import argparse, csv, json, re, sys
from urllib.parse import urlparse

# Что считаем карточкой товара/объявления/мастера на разных площадках
PATTERNS = {
    "avito.ru":       r"/[a-z0-9_\-]+/[a-z0-9_\-]+/[a-z0-9_\-]+_\d{6,}",
    "livemaster.ru":  r"/item/\d+",
    "livemaster.com": r"/item/\d+",
    "uslugi.yandex.ru": r"/profile/",
    "2gis.ru":        r"/firm/\d+",
}

HARVEST_JS = r"""
() => {
  const out = [], seen = new Set();
  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  for (const a of document.querySelectorAll('a[href]')) {
    let href;
    try { href = new URL(a.href, location.href).href.split('?')[0].split('#')[0]; }
    catch (e) { continue; }
    if (!href.startsWith('http') || seen.has(href)) continue;
    // подпись: текст ссылки, иначе title, иначе alt картинки внутри
    let title = clean(a.innerText) || clean(a.getAttribute('title'));
    if (!title) { const img = a.querySelector('img'); if (img) title = clean(img.getAttribute('alt')); }
    if (!title) continue;
    // ближайший контейнер карточки — из него пробуем цену и гео
    let box = a, hops = 0;
    while (box.parentElement && hops < 5) { box = box.parentElement; hops++;
      if (box.innerText && box.innerText.length > title.length + 20) break; }
    const grab = (re) => { const m = (box.innerText || '').match(re); return m ? m[0].trim() : ''; };
    seen.add(href);
    out.push({
      title: title.slice(0, 200),
      url: href,
      price: grab(/\d[\d\s  ]{2,}\s?(?:₽|руб)/),
      geo:   grab(/(?:^|\n)[А-ЯЁ][а-яё\-]+(?:\s[А-ЯЁ][а-яё\-]+)?(?=\n|$)/m),
    });
  }
  return {url: location.href, title: document.title, items: out};
}
"""


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def pattern_for(url):
    host = urlparse(url).netloc.replace("www.", "")
    for dom, pat in PATTERNS.items():
        if host.endswith(dom):
            return re.compile(pat)
    return None


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="harvest")
    p.add_argument("--cdp", default="http://localhost:9222")
    p.add_argument("--all-links", action="store_true",
                   help="не фильтровать по шаблону площадки — забрать все ссылки с подписью")
    a = p.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Нужен Playwright:  pip install playwright  &&  playwright install chromium")

    rows, seen = [], set()
    pw = sync_playwright().start()
    try:
        try:
            browser = pw.chromium.connect_over_cdp(a.cdp)
        except Exception as e:
            sys.exit(f"Не удалось подключиться к Chrome по {a.cdp}: {e}\n"
                     "Закройте все окна Chrome и запустите его с --remote-debugging-port=9222")

        log("Подключился к вашему браузеру.\n")
        log("Откройте нужную выдачу, промотайте её до конца, вернитесь сюда и нажмите Enter.")
        log("Enter — считать текущую страницу.   q + Enter — сохранить и выйти.\n")

        while True:
            cmd = input("[Enter — считать / q — выход] ").strip().lower()
            if cmd == "q":
                break

            pages = []
            for c in browser.contexts:
                pages += [pg for pg in c.pages if not pg.is_closed()]
            pages = [pg for pg in pages if (pg.url or "").startswith("http")]
            if not pages:
                log("  Нет открытых http-вкладок.")
                continue
            # предпочитаем вкладку знакомой площадки, иначе последнюю
            page = next((pg for pg in pages if pattern_for(pg.url)), pages[-1])
            log(f"  читаю вкладку: {page.url[:110]}")
            try:
                data = page.evaluate(HARVEST_JS)
            except Exception as e:
                log(f"  ! не удалось прочитать страницу: {str(e)[:150]}")
                continue

            pat = None if a.all_links else pattern_for(data["url"])
            log(f"  ссылок на странице: {len(data['items'])}"
                + ("" if a.all_links else f", фильтр: {'есть' if pat else 'нет'}"))
            new = 0
            for it in data["items"]:
                u = it["url"]
                if u in seen:
                    continue
                if pat and not pat.search(urlparse(u).path):
                    continue
                seen.add(u)
                new += 1
                rows.append({
                    "Заголовок": it["title"],
                    "Ссылка": u,
                    "Цена": it["price"],
                    "Гео": it["geo"],
                    "Источник": data["url"],
                })
            note = "" if (pat or a.all_links) else "  (площадка незнакомая — фильтр не применялся)"
            log(f"  {data['title'][:60]}… -> +{new} новых, всего {len(rows)}{note}")
            if new == 0 and not a.all_links:
                log("  Если карточки на экране есть, а счёт 0 — перезапустите с --all-links")
    except KeyboardInterrupt:
        log("\nПрервано — сохраняю собранное.")
    finally:
        if rows:
            json.dump(rows, open(a.out + ".json", "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            with open(a.out + ".csv", "w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                w.writeheader(); w.writerows(rows)
            log(f"\nЗаписано {len(rows)} строк -> {a.out}.json / {a.out}.csv")
        else:
            log("\nНичего не собрано.")
        try:
            pw.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
