# -*- coding: utf-8 -*-
"""Кандидаты с Авито и VK-конкуренты по 3D-печати. Все — в лист «Требует проверки»:
у объявлений Авито продавец анонимен, профиль надо открывать вручную."""
import csv, re, collections, os
D2 = "2026-09-16"
M = {"cat":"Категория","name":"Название мастерской / имя мастера","nick":"Никнейм","city":"Город",
     "reg":"Регион","makes":"Что производит","proof":"Признаки собственного производства",
     "act":"Активность","last":"Последняя обнаруженная активность","how":"Основной способ связи",
     "main":"Основной контакт","vk":"VK","tg":"Telegram","ig":"Instagram / другая соцсеть",
     "site":"Сайт","email":"Email","extra":"Дополнительные контакты","src":"Основной источник",
     "src2":"Дополнительный источник","why":"Почему подходит нашей мастерской",
     "parts":"Потенциально интересующие детали / услуги","rel":"Коммерческая релевантность 1-5",
     "q":"Качество лида A/B/C","note":"Комментарий исследователя"}
def L(**k):
    r = {"Дата проверки": D2}
    for a,b in M.items(): r[b] = k.get(a,"")
    return r

CITY_RU = {
 "moskva":"Москва","sankt-peterburg":"Санкт-Петербург","ryazan":"Рязань","minusinsk":"Минусинск",
 "yaroslavl":"Ярославль","novokuznetsk":"Новокузнецк","petrozavodsk":"Петрозаводск","vologda":"Вологда",
 "tambov":"Тамбов","mytischi":"Мытищи","schelkovo":"Щёлково","kolomna":"Коломна","kudrovo":"Кудрово",
 "novosibirsk":"Новосибирск","ekaterinburg":"Екатеринбург","nizhniy_novgorod":"Нижний Новгород",
 "chelyabinsk":"Челябинск","krasnoyarsk":"Красноярск","samara":"Самара","krasnodar":"Краснодар",
 "voronezh":"Воронеж","belgorod":"Белгород","staryy_oskol":"Старый Оскол","magnitogorsk":"Магнитогорск",
 "naberezhnye_chelny":"Набережные Челны","balakovo":"Балаково","taldom":"Талдом","zmievka":"Змиёвка",
 "gostagaevskaya":"Гостагаевская","goryachiy_klyuch":"Горячий Ключ","tashla":"Ташла",
 "ivanovskaya_oblast_shuya":"Шуя, Ивановская обл.","novogornyy":"Новогорный",
 "kemerovo":"Кемерово","rybinsk":"Рыбинск","tolyatti":"Тольятти","omsk":"Омск","pskov":"Псков",
 "volgograd":"Волгоград","himki":"Химки","zhukovskiy":"Жуковский","bataysk":"Батайск",
}
# ключевые слова -> что именно производит
KIND = [
 (re.compile(r'3d[_ ]?pechat|pechat.*osnov|osnov.*pechat|tpu|plastikov', re.I),
  "3D-ПЕЧАТЬ основ и пластиковых деталей для фурсьютов", 5),
 (re.compile(r'protogen|mordochk', re.I), "Мордочки и детали для протогенов на заказ", 5),
 (re.compile(r'glaza', re.I), "Глаза для фурсьютов", 4),
 (re.compile(r'osnov.*na[_ ]?zakaz|osnov.*pod[_ ]?zakaz|osnova dlya fursyuta na zakaz', re.I),
  "Основы фурсьют-голов НА ЗАКАЗ", 4),
 (re.compile(r'osnov', re.I), "Основы фурсьют-голов (готовые)", 3),
 (re.compile(r'na[_ ]?zakaz|pod[_ ]?zakaz', re.I), "Фурсьюты и детали на заказ", 3),
]

def build(csv_path):
    if not os.path.exists(csv_path):
        return []
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    best = {}
    for r in rows:
        u = r["Ссылка на объявление"]
        m = re.match(r'https://www\.avito\.ru/([^/]+)/([^/]+)/(.+?)_(\d+)$', u)
        if not m:
            continue
        city, _cat, slug, _id = m.groups()
        text = slug.replace("_", " ")
        kind = rel = None
        for pat, k, rl in KIND:
            if pat.search(text):
                kind, rel = k, rl
                break
        if not kind or rel < 4:          # берём только сильные сигналы изготовления деталей
            continue
        key = (city, kind)
        if key in best:                  # по одному объявлению на связку город+профиль
            continue
        best[key] = L(
            cat="Fursuit parts maker",
            name=f"Продавец Авито: {text[:60]}",
            nick="—", city=CITY_RU.get(city, city.replace("_", " ")), reg="",
            makes=kind,
            proof=f"Действующее объявление на Авито: «{text[:90]}», цена {r['Цена'] or 'не указана'}",
            act="Объявление активно" + (f", обновлено: {r['Дата объявления']}" if r["Дата объявления"] else ""),
            last=r["Дата объявления"] or "дата в выдаче не показана",
            how="Объявление на Авито", main=u, src=u,
            src2="Сбор объявлений Авито (avito_collect.py, 2026-09-16)",
            why=("Изготавливает ровно ту номенклатуру, которую предлагаем мы. Это либо конкурент "
                 "в своём городе, либо коллега, которому можно отдавать перегруз, либо заказчик "
                 "на то, что его оборудование не тянет. В любом случае — ориентир по ценам рынка."),
            parts="Основы голов, глаза, зубы, когти, крепления, мастер-модели",
            rel=rel, q="Требует проверки",
            note=("ПРОДАВЕЦ АНОНИМЕН: скрипт намеренно не раскрывал телефоны и не открывал карточки — "
                  "имя и профиль мастера нужно посмотреть вручную по ссылке. "
                  "Пока это не лид, а подтверждённый след."))
    return list(best.values())

AVITO = build(os.path.join(os.path.dirname(os.path.abspath(__file__)), "incoming_avito_fursuit.csv"))

# ---- VK-сообщества 3D-печати, найденные попутно: конкуренты и возможные партнёры ----
def P3D(name, nick, city, reg, extra_note=""):
    return L(cat="Prop maker", name=f"[КОНКУРЕНТ/ПАРТНЁР] {name}", nick=nick, city=city, reg=reg,
        makes="Услуги 3D-печати (в том числе для косплея)",
        proof="VK-сообщество сервиса 3D-печати, найдено по геометке города",
        act="Не подтверждена", last="Не подтверждена", how="VK",
        main=f"https://vk.com/{nick}", vk=f"https://vk.com/{nick}",
        src=f"https://vk.com/{nick}", src2="VK-поиск по геометке (vk_collect.py search)",
        why=("НЕ ЛИД: это сервис 3D-печати, то есть наш профиль. Ценность — конкурентная разведка "
             "по региону: цены, сроки, загрузка. В городах, где своих мощностей мало, такие "
             "сервисы иногда берут субподряд."),
        parts="—", rel=1, q="Требует проверки",
        note=("Внесён как конкурентная разведка, не как потенциальный клиент. " + extra_note).strip())

COMPETITORS_3D = [
 P3D("OCTOCAT | Косплей | 3D печать | Квесты | Электроника","octocat_workshop","Челябинск","Челябинская область",
     "ОСОБЫЙ СЛУЧАЙ: сочетает косплей, 3D-печать, квесты и электронику — то есть одновременно и конкурент, и возможный партнёр по сложным заказам."),
 P3D("Kenser Craft — косплей, оружие, броня, 3D-печать","craftlr","Пермь","Пермский край",
     "Делает и косплей-изделия, и печать — ближайший аналог нашей модели в Перми."),
 P3D("LithoPrint | SLA & FDM 3D-печать","lithoprinting","Казань","Республика Татарстан"),
 P3D("DREAMCUBE — студия 3D-печати","dreamcubeshop","Нижний Новгород","Нижегородская область"),
 P3D("3Dpunk — 3D печать","print3dpunk","Нижний Новгород","Нижегородская область"),
 P3D("ЭВРИКА! — 3D-печать, сканирование, моделирование","eureka_3d","Самара","Самарская область",
     "Есть 3D-сканирование — полезно для снятия геометрии с существующих изделий."),
 P3D("3D Печать FDM SLA (kavelin.pro)","ussshuhfyasgfuawf","Челябинск","Челябинская область"),
 P3D("SAKURA 3D печать","sakura_3d","Пермь","Пермский край"),
 P3D("3D печать в Красноярске от ViTcore","3dkrasnoyarsk","Красноярск","Красноярский край"),
 P3D("innovo3d — 3Д печать","innovo3dkrsk","Красноярск","Красноярский край"),
 P3D("UNIVERSE 3D — миниатюры, диорамы, фигурки","3duni","Краснодар","Краснодарский край"),
]

NEW_UNVERIFIED = AVITO + COMPETITORS_3D
