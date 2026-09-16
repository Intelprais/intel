# -*- coding: utf-8 -*-
import json, importlib.util, re, collections
def load(p, attr):
    s = importlib.util.spec_from_file_location(p.split('.')[0], f"/home/user/intel/research/{p}")
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return getattr(m, attr)

fur = load("leads_fursuit.py","FURSUIT")
cos = load("leads_cosplay.py","COSPLAY")
unv = load("leads_unverified.py","UNVERIFIED")

# --- контроль дедупликации: ищем повторяющиеся ссылки между записями ---
seen = {}
dups = []
for grp, rows in (("Fursuit",fur),("Cosplay",cos),("Требует проверки",unv)):
    for r in rows:
        blob = " ".join(str(r.get(c,"")) for c in
                        ["VK","Telegram","Instagram / другая соцсеть","Сайт","Email","Основной контакт"])
        for u in set(re.findall(r'https?://[^\s;,)]+|[\w.\-]+@[\w.\-]+', blob)):
            u = u.rstrip('/.,')
            key = u.lower()
            if key in seen and seen[key][1] != r["Название мастерской / имя мастера"]:
                dups.append((u, seen[key], (grp, r["Название мастерской / имя мастера"])))
            seen[key] = (grp, r["Название мастерской / имя мастера"])
if dups:
    print("!! ВОЗМОЖНЫЕ ДУБЛИ (один контакт у разных записей):")
    for u,a,b in dups: print(f"   {u}\n      {a}\n      {b}")
else:
    print("Дедупликация: пересекающихся контактов между записями не найдено.")

json.dump({"fursuit":fur,"cosplay":cos,"unverified":unv},
          open("leads.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"\nИтог: Fursuit={len(fur)} Cosplay={len(cos)} Требует проверки={len(unv)}")
for grp,rows in (("Fursuit",fur),("Cosplay",cos)):
    c = collections.Counter(r["Качество лида A/B/C"] for r in rows)
    print(f"  {grp}: " + ", ".join(f"{k}={v}" for k,v in sorted(c.items())))
