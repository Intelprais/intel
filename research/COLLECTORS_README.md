# Сборщики данных — инструкция

Три скрипта добирают то, что облачная сессия достать не смогла: VK, Авито и Ярмарка Мастеров
не отвечают внешнему запросу. Все три собирают ТОЛЬКО ФАКТЫ (даты, города, ссылки, счётчики).
Решения о качестве лида, дедупликацию и оценку A/B/C они не делают — это остаётся человеку.

Ни один из них не обходит CAPTCHA: при её появлении скрипт останавливается и сохраняет собранное.

---

## Шаг 1. Подготовка (один раз)

```bash
git clone https://github.com/Intelprais/intel.git
cd intel/research
pip install playwright
playwright install chromium
```
Playwright нужен для Авито и Ярмарки. Для VK через API он не нужен.

---

## Шаг 2. Запуск браузера (для Авито и Ярмарки)

**Сначала закройте ВСЕ окна Chrome полностью** — иначе флаг игнорируется и порт не откроется.

Windows (PowerShell):
```
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="C:\chrome-claude"
```

macOS:
```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-claude
```

Linux:
```bash
google-chrome --remote-debugging-port=9222 --user-data-dir=/tmp/chrome-claude
```

`--user-data-dir` намеренно отдельный: это чистый профиль, изолированный от вашего основного.

**Проверка:** откройте `http://localhost:9222/json/version` — должен вернуться JSON.
Если страница не грузится, Chrome запустился без флага: закройте все окна и повторите.

Логиниться для Авито и Ярмарки не обязательно — выдача видна и так.
Для VK в браузерном режиме зайти в аккаунт нужно (вручную, в этом же окне).

---

## Шаг 3. VK — главный по отдаче, начинать с него

Токен: vk.com/dev -> создать Standalone-приложение -> user access token со scope `groups,offline`.

```bash
export VK_TOKEN=ваш_токен          # Windows: set VK_TOKEN=ваш_токен

# 3.1 Проверить активность всех 41 VK-страницы из базы (~2-3 мин)
python3 vk_collect.py check --leads leads.json --out vk_report

# 3.2 Закрыть непокрытые города
python3 vk_collect.py search \
  --queries "фурсьют на заказ" "фурсьют мастерская" "основа фурсьюта" "косплей мастерская" \
  --cities Казань "Нижний Новгород" Ростов-на-Дону Самара Челябинск Пермь Красноярск \
  --out vk_search
```

Без токена (медленнее и менее надёжно, VK часто меняет вёрстку):
```bash
python3 vk_collect.py check --mode browser --leads leads.json --out vk_report
```

---

## Шаг 4. Ярмарка Мастеров — второй по отдаче

Ценна тем, что у каждого товара есть профиль мастера с городом, то есть выход сразу на мастера.

```bash
# фурсьюты и маски, сразу свернуть в список мастеров
python3 livemaster_collect.py --queries "фурсьют" "маска животного" "голова животного" \
  --pages 3 --by-master --out lm_fursuit

# косплей и реквизит
python3 livemaster_collect.py --queries "косплей костюм" "косплей реквизит" "латексная маска" \
  --pages 2 --by-master --out lm_cosplay
```
С флагом `--by-master` пишутся два файла: сводка по мастерам и `*_items` с товарами.

---

## Шаг 5. Авито — последним, он самый хрупкий

```bash
python3 avito_collect.py --queries "фурсьют" "фурсьют на заказ" "основа фурсьюта" \
  "голова фурсьюта" --region all --pages 3 --out avito_fursuit

python3 avito_collect.py --queries "косплей на заказ" "косплей реквизит" "бутафорское оружие" \
  --regions kazan nizhniy_novgorod rostov-na-donu samara chelyabinsk perm krasnoyarsk \
  --pages 2 --out avito_cosplay
```

Коды регионов — то, что стоит в URL Авито после домена: `moskva`, `sankt-peterburg`, `kazan`,
`nizhniy_novgorod`, `rostov-na-donu`, `samara`, `chelyabinsk`, `perm`, `krasnoyarsk`,
`ekaterinburg`, `novosibirsk`, `krasnodar`, `ufa`, `voronezh`, `all`.

Телефоны скрипт НЕ раскрывает: кнопка «показать телефон» — отдельное действие по каждому
объявлению с повышенным риском для аккаунта. Профиля продавца обычно достаточно.

---

## Шаг 6. Что делать с результатами

Пришлите получившиеся `.csv` — они разносятся по `leads_fursuit.py` / `leads_cosplay.py` /
`leads_unverified.py`, после чего:

```bash
python3 assemble.py && python3 build_outputs.py
```

`assemble.py` заодно проверит, не появилось ли дублей по контактам между записями.

---

## Если что-то пошло не так

| Симптом | Причина и что делать |
|---|---|
| «Не удалось подключиться к Chrome» | Chrome запущен без флага. Закройте ВСЕ окна и перезапустите по шагу 2 |
| «пусто» на непустой выдаче | Сайт изменил вёрстку. Пришлите кусок HTML страницы — поправлю селекторы |
| Остановка на CAPTCHA | Пройдите её вручную в том же окне Chrome, уменьшите `--pages` до 1, запустите снова |
| VK: `5: User authorization failed` | Токен истёк или выдан без scope `groups`. Получите заново |
| VK: `30: This profile is private` | Страница закрыта — это нормально, попадёт в отчёт как ошибка |

Порядок по отдаче: **VK > Ярмарка Мастеров > Авито**. Если времени мало, делайте только шаг 3.
