#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
livemaster_collect.py — сбор мастеров с Ярмарки Мастеров (livemaster.ru)
через УЖЕ ЗАПУЩЕННЫЙ Chrome (CDP).

Ярмарка Мастеров ценна тем, что у каждого товара есть ПРОФИЛЬ МАСТЕРА
с городом и другими работами — то есть выход сразу на мастера, а не на разовое объявление.
Внешнему запросу сайт не отвечает (уходит в цикл редиректов), поэтому нужен браузер.

Собирает только факты: название работы, цена, город, имя и ссылка на магазин мастера.
Решения о качестве лида не принимает.

Примеры:
  python3 livemaster_collect.py --queries "фурсьют" "маска животного" "голова животного" \\
      "косплей маска" --pages 3 --out lm_fursuit

  python3 livemaster_collect.py --queries "косплей костюм" "косплей реквизит" "латексная маска" \\
      --pages 2 --out lm_cosplay

  # сразу агрегировать по мастерам (по одной строке на мастера, а не на товар)
  python3 livemaster_collect.py --queries "фурсьют" --pages 3 --by-master --out lm_masters
"""
import argparse, csv, json, random, sys, time
from collections import OrderedDict
from urllib.parse import quote

BASE = "https://www.livemaster.ru"
BLOCK_MARKERS = ["Подтвердите, что вы не робот", "Доступ ограничен", "Слишком много запросов"]


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def human_pause(lo, hi):
    time.sleep(random.uniform(lo, hi))


def connect(cdp):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Нужен Playwright:  pip install playwright  &&  playwright install chromium")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(cdp)
    except Exception as e:
        pw.stop()
        sys.exit(f"Не удалось подключиться к Chrome по {cdp}: {e}\n"
                 "Закройте все окна Chrome и запустите его с --remote-debugging-port=9222")
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    return pw, browser, ctx


def wait_for_human(page, what="проверку"):
    """CAPTCHA решает ЧЕЛОВЕК, не скрипт. Останавливаемся и ждём.
    Возвращает True, если после вмешательства страница чистая."""
    log("")
    log("=" * 64)
    log(f"  Сайт показал {what}.")
    log("  Пройдите её ВРУЧНУЮ в окне Chrome (вкладка уже открыта),")
    log("  затем вернитесь сюда и нажмите Enter — продолжу с того же места.")
    log("  q + Enter — остановиться и сохранить собранное.")
    log("=" * 64)
    try:
        ans = input("  [Enter — продолжить / q — выход] ").strip().lower()
    except EOFError:
        return False
    if ans == "q":
        return False
    try:
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(2000)
    except Exception:
        pass
    return True


def blocked(page):
    try:
        body = page.inner_text("body")[:3000].lower()
    except Exception:
        return False
    return any(m.lower() in body for m in BLOCK_MARKERS)


def parse_cards(page):
    """Карточки товаров. Ярмарка меняет классы, поэтому есть запасной проход по ссылкам /item/."""
    js = r"""
    () => {
      const out = [], seen = new Set();
      const txt = (root, sel) => { const e = root.querySelector(sel); return e ? e.innerText.trim() : ''; };
      let cards = document.querySelectorAll('[class*="item-tile"], [data-item-id], article');
      for (const c of cards) {
        const a = c.querySelector('a[href*="/item/"]');
        if (!a) continue;
        const url = a.href.split('?')[0];
        if (seen.has(url)) continue;
        seen.add(url);
        const shop = c.querySelector('a[href^="https://www.livemaster.ru/"]:not([href*="/item/"]):not([href*="/subcategory"]):not([href*="/topic"])');
        out.push({
          title: (a.getAttribute('title') || a.innerText || '').trim().slice(0, 200),
          url,
          price: txt(c, '[class*="price"]'),
          city: txt(c, '[class*="city"], [class*="geo"], [class*="location"]'),
          shopName: shop ? shop.innerText.trim() : '',
          shopUrl: shop ? shop.href.split('?')[0] : '',
        });
      }
      if (out.length === 0) {
        for (const a of document.querySelectorAll('a[href*="/item/"]')) {
          const url = a.href.split('?')[0];
          if (seen.has(url)) continue;
          seen.add(url);
          out.push({title:(a.getAttribute('title')||a.innerText||'').trim().slice(0,200),
                    url, price:'', city:'', shopName:'', shopUrl:''});
        }
      }
      return out;
    }
    """
    try:
        return page.evaluate(js)
    except Exception as e:
        log(f"  ! разбор не удался: {e}")
        return []


def collect(ctx, queries, pages, rows):
    page = ctx.new_page()
    page.set_default_timeout(45000)
    seen = set()
    try:
        for i, q in enumerate(queries, 1):
            for pno in range(1, pages + 1):
                url = f"{BASE}/search/item?q={quote(q)}" + (f"&p={pno}" if pno > 1 else "")
                log(f"[{i}/{len(queries)}] {q!r} — стр. {pno}")
                try:
                    page.goto(url, wait_until="domcontentloaded")
                except Exception as e:
                    log(f"  ! не открылось: {str(e)[:120]}")
                    break
                human_pause(2.0, 4.0)
                # выдача догружается лениво — проматываем
                for _ in range(3):
                    page.mouse.wheel(0, 4000)
                    page.wait_for_timeout(900)

                tries = 0
                while blocked(page) and tries < 3:
                    tries += 1
                    if not wait_for_human(page, "проверку"):
                        log(f"   Останавливаюсь. Собрано: {len(rows)} строк — сохраняю.")
                        return
                if blocked(page):
                    log("   Проверка не снялась. Останавливаюсь, сохраняю собранное.")
                    return

                items = parse_cards(page)
                if not items:
                    log("  (пусто — конец выдачи или изменилась вёрстка)")
                    break
                new = 0
                for it in items:
                    if it["url"] in seen:
                        continue
                    seen.add(it["url"])
                    new += 1
                    rows.append({
                        "Запрос": q,
                        "Название работы": it["title"],
                        "Цена": it["price"],
                        "Город": it["city"],
                        "Мастер": it["shopName"],
                        "Профиль мастера": it["shopUrl"],
                        "Ссылка на работу": it["url"],
                    })
                log(f"  +{new} новых (всего {len(rows)})")
                human_pause(2.5, 5.0)
    finally:
        try:
            page.close()
        except Exception:
            pass


def by_master(rows):
    """Схлопывает товары в одну строку на мастера — так удобнее заводить лиды."""
    agg = OrderedDict()
    for r in rows:
        key = r["Профиль мастера"] or r["Мастер"]
        if not key:
            continue
        a = agg.setdefault(key, {"Мастер": r["Мастер"], "Профиль мастера": r["Профиль мастера"],
                                 "Город": r["Город"], "Работ найдено": 0,
                                 "Запросы": set(), "Примеры работ": []})
        a["Работ найдено"] += 1
        a["Запросы"].add(r["Запрос"])
        a["Город"] = a["Город"] or r["Город"]
        if len(a["Примеры работ"]) < 3:
            a["Примеры работ"].append(r["Ссылка на работу"])
    out = []
    for a in agg.values():
        out.append({"Мастер": a["Мастер"], "Профиль мастера": a["Профиль мастера"],
                    "Город": a["Город"], "Работ найдено": a["Работ найдено"],
                    "По каким запросам": "; ".join(sorted(a["Запросы"])),
                    "Примеры работ": " ; ".join(a["Примеры работ"])})
    out.sort(key=lambda x: -x["Работ найдено"])
    return out


def write(rows, out_base):
    if not rows:
        log("Пусто — нечего записывать.")
        return
    json.dump(rows, open(out_base + ".json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    with open(out_base + ".csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    log(f"\nЗаписано {len(rows)} строк -> {out_base}.json / {out_base}.csv")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--queries", nargs="+", required=True)
    p.add_argument("--pages", type=int, default=2)
    p.add_argument("--by-master", action="store_true",
                   help="агрегировать: одна строка на мастера вместо строки на товар")
    p.add_argument("--cdp", default="http://localhost:9222")
    p.add_argument("--out", default="lm_report")
    a = p.parse_args()

    rows = []
    pw, browser, ctx = connect(a.cdp)
    try:
        collect(ctx, a.queries, a.pages, rows)
    except KeyboardInterrupt:
        log("\nПрервано вручную — сохраняю собранное.")
    finally:
        write(by_master(rows) if a.by_master else rows, a.out)
        if a.by_master and rows:
            write(rows, a.out + "_items")
        try:
            pw.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
