# -*- coding: utf-8 -*-
"""Склейка лидов: базовые файлы + обновления по факту проверки VK + новые находки.
Порядок: загрузить -> применить обновления -> перенести мёртвых -> поднять воскресших
-> добавить новых -> проверить дубли -> записать leads.json."""
import json, importlib.util, re, collections, sys

def load(fname, *attrs):
    s = importlib.util.spec_from_file_location(fname.split('.')[0],
                                               f"/home/user/intel/research/{fname}")
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
    return [getattr(m, a) for a in attrs] if len(attrs) > 1 else getattr(m, attrs[0])

NAME = "Название мастерской / имя мастера"
Q = "Качество лида A/B/C"
NOTE = "Комментарий исследователя"

fur = load("leads_fursuit.py", "FURSUIT")
cos = load("leads_cosplay.py", "COSPLAY")
unv = load("leads_unverified.py", "UNVERIFIED")
U, DROP, PROMOTE = load("updates_from_vk.py", "U", "DROP", "PROMOTE")
new_fur, new_cos = load("leads_new_vk.py", "NEW_FURSUIT", "NEW_COSPLAY")
new_unv = load("leads_new_avito.py", "NEW_UNVERIFIED")

# короткие ключи -> имена колонок (как в leads_*.py)
KEY = {"cat":"Категория","name":NAME,"nick":"Никнейм","city":"Город","reg":"Регион",
       "makes":"Что производит","proof":"Признаки собственного производства","act":"Активность",
       "last":"Последняя обнаруженная активность","how":"Основной способ связи",
       "main":"Основной контакт","vk":"VK","tg":"Telegram","ig":"Instagram / другая соцсеть",
       "site":"Сайт","email":"Email","extra":"Дополнительные контакты","src":"Основной источник",
       "src2":"Дополнительный источник","why":"Почему подходит нашей мастерской",
       "parts":"Потенциально интересующие детали / услуги",
       "rel":"Коммерческая релевантность 1-5","q":Q,"note":NOTE}

# ---------- 1. применить обновления ----------
applied, missing = 0, []
for name, upd in U.items():
    hit = False
    for group in (fur, cos, unv):
        for r in group:
            if r[NAME] == name:
                for k, v in upd.items():
                    col = KEY.get(k, k)
                    if k == "note":          # комментарий дополняем, а не затираем
                        r[col] = (r[col] + "  ///  ОБНОВЛЕНО " + v) if r[col] else v
                    else:
                        r[col] = v
                r["Дата проверки"] = "2026-09-16"
                hit = True; applied += 1
    if not hit:
        missing.append(name)

# ---------- 2. мёртвые -> «Требует проверки» ----------
dropped = 0
for name, reason in DROP.items():
    for group in (fur, cos):
        for r in list(group):
            if r[NAME] == name:
                r[Q] = "Требует проверки"
                r["Активность"] = "Не работает / страница недоступна"
                r["Последняя обнаруженная активность"] = "проверено 2026-09-16"
                r["Дата проверки"] = "2026-09-16"
                r[NOTE] = (r[NOTE] + "  ///  ПЕРЕНЕСЕНО из основной базы: " + reason)
                group.remove(r); unv.append(r); dropped += 1

# ---------- 3. воскресшие -> в основную базу ----------
promoted = 0
for name, upd in PROMOTE.items():
    for r in list(unv):
        if r[NAME] == name:
            target = upd.pop("to", "fursuit")
            for k, v in upd.items():
                col = KEY.get(k, k)
                if k == "note":
                    r[col] = (r[col] + "  ///  ОБНОВЛЕНО " + v) if r[col] else v
                else:
                    r[col] = v
            r["Дата проверки"] = "2026-09-16"
            unv.remove(r)
            (fur if target == "fursuit" else cos).append(r)
            promoted += 1

# ---------- 4. новые ----------
fur += new_fur; cos += new_cos; unv += new_unv

# ---------- 5. контроль дублей ----------
seen, dups = {}, []
for grp, rows in (("Fursuit", fur), ("Cosplay", cos), ("Требует проверки", unv)):
    for r in rows:
        blob = " ".join(str(r.get(c, "")) for c in
                        ["VK","Telegram","Instagram / другая соцсеть","Сайт","Email","Основной контакт"])
        for u in set(re.findall(r'https?://[^\s;,)]+|[\w.\-]+@[\w.\-]+', blob)):
            u = u.rstrip('/.,').lower()
            if u in seen and seen[u][1] != r[NAME]:
                dups.append((u, seen[u], (grp, r[NAME])))
            seen[u] = (grp, r[NAME])

print(f"Обновлено полей у записей: {applied}")
if missing: print(f"!! не найдены для обновления: {missing}")
print(f"Перенесено в «Требует проверки»: {dropped}")
print(f"Поднято в основную базу: {promoted}")
print(f"Добавлено новых: fursuit +{len(new_fur)}, cosplay +{len(new_cos)}, требует проверки +{len(new_unv)}")
if dups:
    print("\n!! ВОЗМОЖНЫЕ ДУБЛИ (один контакт у разных записей):")
    for u, a, b in dups: print(f"   {u}\n      {a}\n      {b}")
else:
    print("\nДедупликация: пересекающихся контактов между записями не найдено.")

json.dump({"fursuit": fur, "cosplay": cos, "unverified": unv},
          open("leads.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"\nИтог: Fursuit={len(fur)} Cosplay={len(cos)} Требует проверки={len(unv)}")
for grp, rows in (("Fursuit", fur), ("Cosplay", cos)):
    c = collections.Counter(r[Q] for r in rows)
    print(f"  {grp}: " + ", ".join(f"{k}={v}" for k, v in sorted(c.items())))
