#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vk_collect.py — сбор фактов о VK-страницах для базы лидов.
НЕ принимает решений о качестве лида: только вытаскивает проверяемые факты
(дата последнего поста, число подписчиков, город, контакты, статус) и складывает их в отчёт.
Квалификация, дедупликация и оценка A/B/C остаются человеку/модели.

Два режима:
  api     — через VK API (рекомендуется: быстро, стабильно, без парсинга HTML)
  browser — через уже запущенный Chrome с отладочным портом (если токена нет)

Примеры:
  # 1. Проверить активность всех VK-ссылок из базы
  python3 vk_collect.py check --token $VK_TOKEN --leads leads.json --out vk_report

  # 2. Проверить произвольный список
  python3 vk_collect.py check --token $VK_TOKEN --urls vk.com/catcraft vk.com/wolf_factory

  # 3. Искать сообщества по городам и ключевым словам
  python3 vk_collect.py search --token $VK_TOKEN \
      --queries "фурсьют на заказ" "фурсьют мастерская" "основа фурсьюта" \
      --cities Казань "Нижний Новгород" Ростов-на-Дону Самара Челябинск Пермь Красноярск \
      --out vk_search

  # 4. Браузерный режим (Chrome должен быть запущен с --remote-debugging-port=9222)
  python3 vk_collect.py check --mode browser --leads leads.json --out vk_report

Токен: vk.com/dev -> создать Standalone-приложение -> получить user access token
со scope 'groups,offline'. Токен передавать через переменную окружения, не в истории команд.
"""
import argparse, csv, json, os, re, sys, time, datetime as dt
from urllib.parse import urlencode
from urllib.request import urlopen, Request

API_VERSION = "5.199"
API_URL = "https://api.vk.com/method/"
RATE_SLEEP = 0.40          # ~2.5 запроса/сек, ниже лимита в 3/сек для user-токена
UA = "Mozilla/5.0 (compatible; lead-research/1.0)"

# Города -> city_id VK. Проставляются автоматом через database.getCities,
# здесь только те, что нужны чаще всего, чтобы сэкономить запросы.
CITY_CACHE = {}


# ------------------------------------------------------------------ утилиты

def log(*a):
    print(*a, file=sys.stderr, flush=True)


def months_since(ts):
    if not ts:
        return None
    d = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
    now = dt.datetime.now(dt.timezone.utc)
    return round((now - d).days / 30.44, 1)


def iso(ts):
    if not ts:
        return ""
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")


SCREEN_RE = re.compile(r'(?:https?://)?(?:m\.)?vk\.(?:com|ru)/([A-Za-z0-9_.]+)')

def extract_screen_names(text):
    """Достаёт все vk.com/<screen_name> из строки. Отбрасывает служебные разделы."""
    skip = {"video", "audio", "wall", "feed", "im", "away.php", "search", "dev", "id0"}
    out = []
    for m in SCREEN_RE.finditer(text or ""):
        s = m.group(1).rstrip(".")
        if s and s.lower() not in skip and s not in out:
            out.append(s)
    return out


def leads_vk_urls(path):
    """Возвращает [(название лида, лист, screen_name), ...] из leads.json."""
    data = json.load(open(path, encoding="utf-8"))
    rows = []
    for sheet in ("fursuit", "cosplay", "unverified"):
        for r in data.get(sheet, []):
            name = r.get("Название мастерской / имя мастера", "")
            blob = " ".join(str(r.get(c, "")) for c in
                            ("VK", "Основной контакт", "Дополнительные контакты",
                             "Основной источник", "Дополнительный источник", "Сайт"))
            for sn in extract_screen_names(blob):
                rows.append((name, sheet, sn))
    # дедуп по screen_name, сохраняя первое встреченное имя лида
    seen, uniq = set(), []
    for name, sheet, sn in rows:
        if sn.lower() in seen:
            continue
        seen.add(sn.lower())
        uniq.append((name, sheet, sn))
    return uniq


# ------------------------------------------------------------------ VK API

class VKApi:
    def __init__(self, token):
        if not token:
            sys.exit("Нужен --token или переменная окружения VK_TOKEN. См. шапку файла.")
        self.token = token
        self.calls = 0

    def __call__(self, method, **params):
        params.update(access_token=self.token, v=API_VERSION)
        req = Request(API_URL + method + "?" + urlencode(params), headers={"User-Agent": UA})
        for attempt in range(4):
            try:
                body = json.loads(urlopen(req, timeout=30).read().decode())
            except Exception as e:
                log(f"  ! сеть: {e} (попытка {attempt+1})")
                time.sleep(2 ** attempt)
                continue
            self.calls += 1
            time.sleep(RATE_SLEEP)
            if "error" in body:
                err = body["error"]
                code, msg = err.get("error_code"), err.get("error_msg")
                if code == 6:                      # too many requests
                    time.sleep(1 + attempt)
                    continue
                if code == 14:                     # captcha
                    sys.exit("VK требует CAPTCHA. Скрипт остановлен — обходить её нельзя. "
                             "Подождите или продолжите вручную.")
                return {"__error__": f"{code}: {msg}"}
            return body.get("response")
        return {"__error__": "сеть недоступна после 4 попыток"}

    # --- разрешение имени -> (type, id)
    def resolve(self, screen_name):
        r = self("utils.resolveScreenName", screen_name=screen_name)
        if not r or "__error__" in (r or {}):
            return None, None
        return r.get("type"), r.get("object_id")

    def city_id(self, name):
        if name in CITY_CACHE:
            return CITY_CACHE[name]
        r = self("database.getCities", country_id=1, q=name, count=1)
        cid = None
        if isinstance(r, dict) and r.get("items"):
            cid = r["items"][0]["id"]
        CITY_CACHE[name] = cid
        return cid


def fact_row(lead, sheet, screen, kind, info, last_ts, err=""):
    """Единая строка отчёта. Только факты, без оценок."""
    return {
        "Лид": lead, "Лист": sheet, "VK": f"https://vk.com/{screen}",
        "Тип": kind, "Название в VK": info.get("name", ""),
        "Подписчиков": info.get("members", ""),
        "Город в VK": info.get("city", ""),
        "Статус/описание": (info.get("status") or "")[:200],
        "Сайт из VK": info.get("site", ""),
        "Последний пост": iso(last_ts),
        "Месяцев с последнего поста": months_since(last_ts) if last_ts else "",
        "Стена закрыта/пуста": "да" if last_ts is None and not err else "",
        "Ошибка": err,
    }


def api_check(api, targets):
    out = []
    for i, (lead, sheet, screen) in enumerate(targets, 1):
        log(f"[{i}/{len(targets)}] {screen}")
        kind, oid = api.resolve(screen)
        if not kind:
            out.append(fact_row(lead, sheet, screen, "не найдено", {}, None,
                                "страница не существует или переименована"))
            continue
        info, last_ts, err = {}, None, ""
        if kind == "group":
            g = api("groups.getById", group_ids=screen,
                    fields="description,city,site,members_count,status,is_closed")
            items = (g or {}).get("groups") if isinstance(g, dict) else g
            if isinstance(g, dict) and "__error__" in g:
                err = g["__error__"]
            elif items:
                it = items[0]
                info = {"name": it.get("name", ""), "members": it.get("members_count", ""),
                        "city": (it.get("city") or {}).get("title", ""),
                        "status": it.get("status") or it.get("description", ""),
                        "site": it.get("site", "")}
            owner = -oid
        elif kind == "user":
            u = api("users.get", user_ids=screen, fields="city,status,followers_count,site")
            if isinstance(u, dict) and "__error__" in u:
                err = u["__error__"]
            elif u:
                it = u[0]
                info = {"name": f"{it.get('first_name','')} {it.get('last_name','')}".strip(),
                        "members": it.get("followers_count", ""),
                        "city": (it.get("city") or {}).get("title", ""),
                        "status": it.get("status", ""), "site": it.get("site", "")}
            owner = oid
        else:
            out.append(fact_row(lead, sheet, screen, kind, {}, None, "неподдерживаемый тип"))
            continue

        if not err:
            w = api("wall.get", owner_id=owner, count=3, filter="owner")
            if isinstance(w, dict) and "__error__" in w:
                err = w["__error__"]           # закрытая стена тоже придёт сюда
            elif isinstance(w, dict) and w.get("items"):
                last_ts = max(p.get("date", 0) for p in w["items"])
        out.append(fact_row(lead, sheet, screen, kind, info, last_ts, err))
    return out


def api_search(api, queries, cities):
    out, seen = [], set()
    combos = [(q, c) for q in queries for c in (cities or [None])]
    for i, (q, city) in enumerate(combos, 1):
        cid = api.city_id(city) if city else None
        if city and not cid:
            log(f"[{i}/{len(combos)}] город не распознан: {city}")
            continue
        log(f"[{i}/{len(combos)}] поиск: {q!r}" + (f" / {city}" if city else ""))
        params = dict(q=q, count=100, type="group", sort=0)
        if cid:
            params["city_id"] = cid
        r = api("groups.search", **params)
        if not isinstance(r, dict) or "__error__" in r:
            log(f"  ! {r}")
            continue
        for it in r.get("items", []):
            gid = it.get("id")
            if gid in seen:
                continue
            seen.add(gid)
            out.append({
                "Запрос": q, "Город запроса": city or "",
                "Название": it.get("name", ""),
                "VK": f"https://vk.com/{it.get('screen_name') or 'club'+str(gid)}",
                "Подписчиков": it.get("members_count", ""),
                "Тип": it.get("type", ""),
                "Закрытая": "да" if it.get("is_closed") else "нет",
            })
    return out


# ------------------------------------------------------------------ браузер

def browser_check(targets, cdp="http://localhost:9222"):
    """Запасной режим: читает публичные страницы через уже открытый Chrome."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("Нужен Playwright:  pip install playwright  &&  playwright install chromium")
    out = []
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp(cdp)
        except Exception as e:
            sys.exit(f"Не удалось подключиться к Chrome по {cdp}: {e}\n"
                     "Запустите Chrome с флагом --remote-debugging-port=9222 и залогиньтесь в VK.")
        ctx = browser.contexts[0] if browser.contexts else browser.new_context()
        page = ctx.new_page()
        for i, (lead, sheet, screen) in enumerate(targets, 1):
            log(f"[{i}/{len(targets)}] {screen}")
            info, last_iso, err = {}, "", ""
            try:
                page.goto(f"https://vk.com/{screen}", timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(1800)
                if page.locator("text=Подтвердите, что вы не робот").count():
                    sys.exit("VK показал CAPTCHA. Скрипт остановлен — пройдите её вручную в браузере.")
                title = page.title()
                info["name"] = re.sub(r"\s*\|\s*VK\s*$", "", title).strip()
                # дата последнего поста: VK отдаёт её в атрибуте time или текстом
                for sel in ("._post_date .PostHeaderSubtitle__item",
                            "._post_date", ".post_date .rel_date", ".rel_date"):
                    loc = page.locator(sel).first
                    if loc.count():
                        last_iso = (loc.get_attribute("abs_time")
                                    or loc.get_attribute("title")
                                    or loc.inner_text()).strip()
                        break
                m = page.locator(".header_count, .redesigned-group-info__value").first
                if m.count():
                    info["members"] = m.inner_text().strip()
            except Exception as e:
                err = str(e)[:200]
            row = fact_row(lead, sheet, screen, "страница", info, None, err)
            row["Последний пост"] = last_iso      # как отдал VK, без нормализации
            row["Месяцев с последнего поста"] = ""
            out.append(row)
        page.close()
    return out


# ------------------------------------------------------------------ запись

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
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="проверить активность VK-страниц")
    c.add_argument("--mode", choices=["api", "browser"], default="api")
    c.add_argument("--token", default=os.environ.get("VK_TOKEN"))
    c.add_argument("--leads", help="leads.json — взять все VK-ссылки оттуда")
    c.add_argument("--urls", nargs="*", default=[], help="произвольные vk.com/... или screen_name")
    c.add_argument("--cdp", default="http://localhost:9222")
    c.add_argument("--out", default="vk_report")

    s = sub.add_parser("search", help="искать сообщества по запросам и городам")
    s.add_argument("--token", default=os.environ.get("VK_TOKEN"))
    s.add_argument("--queries", nargs="+", required=True)
    s.add_argument("--cities", nargs="*", default=[])
    s.add_argument("--out", default="vk_search")

    a = p.parse_args()

    if a.cmd == "check":
        targets = []
        if a.leads:
            targets += leads_vk_urls(a.leads)
        for u in a.urls:
            for sn in (extract_screen_names(u) or [u.strip().strip("/")]):
                targets.append(("(вручную)", "-", sn))
        if not targets:
            sys.exit("Нечего проверять: укажите --leads и/или --urls.")
        seen, uniq = set(), []
        for t in targets:
            if t[2].lower() not in seen:
                seen.add(t[2].lower()); uniq.append(t)
        log(f"К проверке: {len(uniq)} страниц, режим {a.mode}\n")
        rows = (browser_check(uniq, a.cdp) if a.mode == "browser"
                else api_check(VKApi(a.token), uniq))
        write(rows, a.out)

    elif a.cmd == "search":
        api = VKApi(a.token)
        rows = api_search(api, a.queries, a.cities)
        log(f"\nЗапросов к API: {api.calls}")
        write(rows, a.out)


if __name__ == "__main__":
    main()
