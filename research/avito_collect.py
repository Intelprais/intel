#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
avito_collect.py — сбор объявлений Авито через УЖЕ ЗАПУЩЕННЫЙ Chrome (CDP).
Собирает только факты из выдачи: заголовок, цена, город, дата, ссылка на объявление
и на профиль продавца. Решения о качестве лида не принимает.

Авито не имеет публичного API и активно блокирует автоматизацию, поэтому:
  - работаем через ваш обычный браузер, медленно, с паузами;
  - CAPTCHA не обходится: при её появлении скрипт останавливается;
  - телефоны по умолчанию НЕ раскрываются (кнопка «показать телефон» — отдельное
    действие с повышенным риском для аккаунта). Профиль продавца обычно достаточен.

Запуск Chrome (закрыв все окна Chrome заранее):
  Windows: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\\chrome-claude"
  macOS:   /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-claude

Примеры:
  # фурсьюты по всей России
  python3 avito_collect.py --queries "фурсьют" "фурсьют на заказ" "основа фурсьюта" \
      "голова фурсьюта" --region all --pages 3 --out avito_fursuit

  # косплей-реквизит по конкретным городам
  python3 avito_collect.py --queries "косплей на заказ" "косплей реквизит" "бутафорское оружие" \
      --regions kazan nizhniy_novgorod rostov-na-donu samara chelyabinsk perm krasnoyarsk \
      --pages 2 --out avito_cosplay

Коды регионов — это то, что стоит в URL Авито после домена: avito.ru/<регион>/...
  all, moskva, sankt-peterburg, kazan, nizhniy_novgorod, rostov-na-donu, samara,
  chelyabinsk, perm, krasnoyarsk, ekaterinburg, novosibirsk, krasnodar, ufa, voronezh
"""
import argparse, csv, json, random, re, sys, time
from urllib.parse import quote

BASE = "https://www.avito.ru"
CAPTCHA_MARKERS = ["Подтвердите, что вы не робот", "Доступ ограничен",
                   "проверки безопасности", "firewall"]


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def human_pause(lo, hi):
    """Неравномерная пауза — и вежливость к сайту, и меньше шансов словить блок."""
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


def check_blocked(page):
    try:
        body = page.inner_text("body")[:3000]
    except Exception:
        return False
    for mark in CAPTCHA_MARKERS:
        if mark.lower() in body.lower():
            return True
    return False


def parse_listing_page(page):
    """Достаёт карточки из выдачи. Авито периодически меняет data-marker —
    поэтому сначала пробуем разметку, затем откатываемся на ссылки вида /items/."""
    js = """
    () => {
      const out = [];
      const seen = new Set();
      const cards = document.querySelectorAll('[data-marker="item"]');
      for (const c of cards) {
        const a = c.querySelector('a[data-marker="item-title"], a[itemprop="url"], h3 a, a[href*="_"]');
        if (!a) continue;
        const href = a.href;
        if (seen.has(href)) continue;
        seen.add(href);
        const txt = (sel) => { const e = c.querySelector(sel); return e ? e.innerText.trim() : ''; };
        out.push({
          title: (a.innerText || '').trim(),
          url: href,
          price: txt('[itemprop="price"], [data-marker="item-price"]'),
          geo: txt('[data-marker="item-address"], .geo-root, [class*="geo-"]'),
          date: txt('[data-marker="item-date"], [class*="date-text"]'),
          seller: txt('[data-marker="seller-info/name"], [class*="seller-info-name"]'),
        });
      }
      if (out.length === 0) {
        for (const a of document.querySelectorAll('a[href*="/items/"], a[data-marker="item-title"]')) {
          if (seen.has(a.href)) continue;
          seen.add(a.href);
          out.push({title: (a.innerText||'').trim(), url: a.href, price:'', geo:'', date:'', seller:''});
        }
      }
      return out;
    }
    """
    try:
        return page.evaluate(js)
    except Exception as e:
        log(f"  ! разбор страницы не удался: {e}")
        return []


def collect(ctx, queries, regions, pages, out_rows):
    page = ctx.new_page()
    page.set_default_timeout(45000)
    combos = [(q, r) for q in queries for r in regions]
    seen_urls = set()
    try:
        for i, (q, region) in enumerate(combos, 1):
            for pno in range(1, pages + 1):
                url = f"{BASE}/{region}?q={quote(q)}" + (f"&p={pno}" if pno > 1 else "")
                log(f"[{i}/{len(combos)}] {region} / {q!r} — стр. {pno}")
                try:
                    page.goto(url, wait_until="domcontentloaded")
                except Exception as e:
                    log(f"  ! не открылось: {str(e)[:120]}")
                    break
                human_pause(2.5, 5.0)

                tries = 0
                while check_blocked(page) and tries < 3:
                    tries += 1
                    if not wait_for_human(page, "проверку/CAPTCHA"):
                        log(f"   Останавливаюсь. Собрано: {len(out_rows)} строк — сохраняю.")
                        return
                if check_blocked(page):
                    log("   Проверка не снялась. Останавливаюсь, сохраняю собранное.")
                    return

                items = parse_listing_page(page)
                if not items:
                    log("  (пусто — либо конец выдачи, либо изменилась вёрстка)")
                    break
                new = 0
                for it in items:
                    u = (it.get("url") or "").split("?")[0]
                    if not u or u in seen_urls:
                        continue
                    seen_urls.add(u)
                    new += 1
                    out_rows.append({
                        "Запрос": q,
                        "Регион запроса": region,
                        "Заголовок": it.get("title", ""),
                        "Цена": it.get("price", ""),
                        "Гео в объявлении": it.get("geo", ""),
                        "Дата объявления": it.get("date", ""),
                        "Продавец": it.get("seller", ""),
                        "Ссылка на объявление": u,
                    })
                log(f"  +{new} новых (всего {len(out_rows)})")
                human_pause(3.0, 6.5)
    finally:
        try:
            page.close()
        except Exception:
            pass


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
    p.add_argument("--regions", nargs="*", default=["all"],
                   help="коды регионов Авито из URL, напр. moskva kazan. По умолчанию all")
    p.add_argument("--region", help="синоним для одного региона")
    p.add_argument("--pages", type=int, default=2, help="страниц выдачи на запрос (по умолчанию 2)")
    p.add_argument("--cdp", default="http://localhost:9222")
    p.add_argument("--out", default="avito_report")
    a = p.parse_args()

    regions = [a.region] if a.region else a.regions
    rows = []
    pw, browser, ctx = connect(a.cdp)
    try:
        collect(ctx, a.queries, regions, a.pages, rows)
    except KeyboardInterrupt:
        log("\nПрервано вручную — сохраняю собранное.")
    finally:
        write(rows, a.out)
        try:
            pw.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
