#!/usr/bin/env python3
"""Historique mensuel des prix des produits scellés Pokémon (TCGplayer, en dollars), pour Cresus Folio.

Source : archives quotidiennes publiques de TCGCSV (https://tcgcsv.com/faq), disponibles depuis le 8 février 2024.
Le script prend une archive par mois (le 1er, ou le premier jour disponible), n'en garde que les produits scellés
Pokémon (catégorie 3, sans numéro ni rareté de carte) et écrit data/historique-pokemon.json :
  { "v": 1, "maj": "AAAA-MM-JJ", "dates": ["2024-02-08", "2024-03-01", ...], "p": { "<productId>": [prix ou null, ...] } }
Il ne télécharge que les mois manquants : on peut le relancer sans risque (tâche mensuelle GitHub).
Les prix d'avant la sortie d'un produit (précommandes, souvent très gonflées) sont écartés : seule compte la date
de sortie du produit (presaleInfo.releasedOn de TCGplayer, sinon celle de son extension), notée dans "sortie".
Pour que la courbe parte bien du jour de sortie (et pas du 1er du mois suivant), le prix de ce jour-là est ajouté
dans "lancement" : { "<productId>": ["AAAA-MM-JJ", prix] } (une archive par date de sortie, téléchargée une seule fois).
"magasin" : { "<productId>": prix } donne le prix en magasin estimé en France (€) des produits sortis depuis février 2024,
d'après le type de produit et le nombre de boosters indiqué dans sa description : la courbe du site part de ce prix.
"""
import datetime as dt
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata

import py7zr
import requests

BASE = 'https://tcgcsv.com'
CATEGORIE = 3  # Pokémon
DEBUT = dt.date(2024, 2, 8)  # première archive existante
SORTIE = os.path.join(os.path.dirname(__file__), '..', 'data', 'historique-pokemon.json')
UA = {'User-Agent': 'CresusFolio-historique/1.0 (+https://cresusfolio.web.app)'}


def get(url, **kw):
    for essai in range(4):
        try:
            r = requests.get(url, headers=UA, timeout=120, **kw)
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r
        except requests.RequestException as e:
            if essai == 3:
                raise
            time.sleep(5 * (essai + 1))


NOMBRES = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
           'eleven': 11, 'twelve': 12, 'thirteen': 13, 'fourteen': 14, 'fifteen': 15, 'sixteen': 16, 'seventeen': 17,
           'eighteen': 18, 'nineteen': 19, 'twenty': 20, 'twenty-five': 25, 'thirty': 30, 'thirty-six': 36, 'a': 1, 'an': 1}


def nombre(mot):
    m = re.sub(r'^(\d+)x$', r'\1', re.sub(r'[(),]', '', (mot or '').lower()))
    return int(m) if re.fullmatch(r'\d{1,3}', m) else NOMBRES.get(m)


def texte_brut(html):
    t = unicodedata.normalize('NFKC', html or '')
    t = re.sub(r'<[^>]+>', ' ', t)
    t = re.sub(r'&[a-z]+;', ' ', t)
    return re.sub(r'\s+', ' ', re.sub(r'[—–]', ' ', t)).strip()


def nb_boosters(t):
    """Nombre de boosters annoncé dans la description (« four booster packs », « 9 Pokémon TCG: … booster packs »)."""
    W, mx = t.split(' '), 0
    for i in range(len(W) - 1):
        if not re.fullmatch(r'booster', W[i], re.I) or not re.match(r'packs?\b', W[i + 1], re.I):
            continue
        pluriel = bool(re.match(r'packs\b', W[i + 1], re.I))
        for j in range(i - 1, max(-1, i - 15), -1):
            if re.search(r'[.;!?]$', W[j]) or re.fullmatch(r'(each|per|every)', W[j], re.I):
                break
            v = nombre(W[j])
            if v is not None:
                suivant = W[j + 1] if j + 1 < len(W) else ''
                if not re.fullmatch(r'cards?', suivant, re.I) and (v == 1 or pluriel):
                    mx = max(mx, v)
                break
    return mx


def nb_unites(t):
    """Nombre d'unités d'un carton ou d'un display (« contains 10 Elite Trainer Boxes », « 12x … »)."""
    m = re.search(r'\b(?:contains?|includes?)\s+(\d{1,3}|two|three|four|five|six|eight|ten|twelve|twenty-five)\b', t, re.I)
    if m:
        return nombre(m.group(1))
    s = sum(int(x) for x in re.findall(r'\b(\d{1,2})x\s', t, re.I))
    return s or None


def prix_magasin(nom, html, sortie):
    """Prix en magasin estimé en France (€) d'après le type de produit et son contenu.
    Prix relevés 2025-2026 (Fnac, Leclerc, Amazon) : ETB 55,99 ; bundle 6 boosters 35,99 ; tripack 17,99 ;
    Pokébox 24,99 ; booster 5,99 puis 6,99 en 2026. Les autres coffrets : environ 6 € par booster + les extras."""
    s = unicodedata.normalize('NFKD', nom.lower()).encode('ascii', 'ignore').decode()
    n = ' ' + re.sub(r'[^a-z0-9&]+', ' ', s.replace("'", '')).strip() + ' '
    t = texte_brut(html)
    B, P = (6.99 if sortie >= '2026-01-01' else 5.99), 6

    def r(v):
        return max(0.99, math.floor(v + 0.5) - 0.01)

    if re.search(r' fun pack | mcdonald| trick or trade ', n):
        return None  # pas vendu en magasin
    mult = 1
    m = re.search(r' set of (\d+|two|three|four) ', n)
    if m:
        mult *= nombre(m.group(1))
    carton = bool(re.search(r' case | master carton ', n)) and ' pencil case ' not in n
    display = ' display ' in n and not re.search(r' booster display | booster box ', n)
    k = nb_unites(t)
    if carton and display:
        mult *= (k or 4) * 10
    elif carton:
        if ' sleeved ' in n:
            d = 144
        elif ' pokemon center elite ' in n:
            d = 4
        elif ' elite trainer ' in n:
            d = 10
        elif re.search(r' booster box | booster display ', n):
            d = 6
        elif ' bundle ' in n:
            d = 25
        elif ' 3 pack ' in n:
            d = 24
        else:
            d = 6
        mult *= k or d
    elif display:
        mult *= k if k and k >= 4 else 10
    m = re.search(r' mini tins (\d+) pack ', n)
    if m:
        mult *= int(m.group(1))
    b = re.sub(r' (case|master carton|display) ', ' ', n)
    pk = nb_boosters(t)
    if ' pokemon center elite trainer ' in b:
        u = 65.99
    elif ' elite trainer ' in b:
        u = 55.99
    elif re.search(r' booster box | booster display ', b):
        q = re.search(r' (\d+) packs? ', b)
        u = r((pk if pk in (18, 24, 36) else int(q.group(1)) if q else 36) * P)
    elif ' booster bundle ' in b and ' art bundle ' not in b:
        u = 35.99
    elif re.search(r' build & battle stadium | build battle stadium ', b):
        u = r(pk * P + 12) if pk else 59.99
    elif re.search(r' build & battle | build battle ', b):
        u = 24.99
    elif re.search(r' mini tins? ', b):
        u = r((pk or 2) * P)
    elif re.search(r' 3 pack | three pack | tripack ', b):
        u = 17.99
    elif ' 2 pack ' in b:
        u = 12.99
    elif re.search(r' single pack blister | checklane ', b):
        u = 7.99
    elif re.search(r' sleeved booster | booster pack ', b):
        u = B
    elif ' league battle deck ' in b:
        u = 29.99
    elif ' deluxe battle deck ' in b:
        u = 19.99
    elif ' battle deck ' in b:
        u = 14.99
    elif ' battle academy ' in b:
        u = 29.99
    elif ' toolkit ' in b:
        u = 39.99
    elif ' ultra premium ' in b:
        u = r(pk * P + 50) if pk >= 10 else 129.99
    elif ' super premium ' in b:
        u = r(pk * P + 20) if pk >= 8 else 79.99
    elif re.search(r' premium | calendar ', b):
        u = r(pk * P + 14) if pk >= 4 else 49.99
    elif ' chest ' in b:
        u = r(pk * P) if pk else 35.99
    elif re.search(r' binder | portfolio ', b):
        u = r(pk * P + 5) if pk else 29.99
    elif ' figure ' in b:
        u = r(pk * P + 6) if pk else 34.99
    elif ' tin ' in b:
        u = r(pk * P + 1) if pk else 24.99
    elif pk:
        u = r(pk * P + 1)
    elif re.search(r' collection | box ', b):
        u = 24.99
    else:
        return None
    return round(u * mult, 2)


def produits_scelles():
    """Produits scellés Pokémon du catalogue actuel de TCGCSV :
    ({id: date de sortie 'AAAA-MM-JJ', ou '' si en précommande}, {id: prix magasin estimé})."""
    groupes = get(f'{BASE}/tcgplayer/{CATEGORIE}/groups').json()['results']
    ids, magasin = {}, {}
    for g in groupes:
        sortie_ext = (g.get('publishedOn') or '')[:10]
        r = get(f"{BASE}/tcgplayer/{CATEGORIE}/{g['groupId']}/products")
        if not r:
            continue
        for p in r.json().get('results', []):
            ext = p.get('extendedData') or []
            nom = p.get('name') or ''
            if any((e.get('name') or '').lower() in ('number', 'rarity') for e in ext):
                continue  # carte à l'unité
            if 'code card' in nom.lower() or 'online code' in nom.lower():
                continue
            pre = p.get('presaleInfo') or {}
            pid = str(p['productId'])
            ids[pid] = '' if pre.get('isPresale') else ((pre.get('releasedOn') or '')[:10] or sortie_ext)
            if ids[pid] >= DEBUT.isoformat():
                texte = next((e.get('value') or '' for e in ext if (e.get('name') or '') == 'CardText'), '')
                v = prix_magasin(nom, texte, ids[pid])
                if v:
                    magasin[pid] = v
        time.sleep(0.05)
    return ids, magasin


def dates_voulues(aujourdhui):
    """Une date par mois : le 8 février 2024, puis le 1er de chaque mois jusqu'au mois courant."""
    D = [DEBUT]
    a, m = 2024, 3
    while dt.date(a, m, 1) <= aujourdhui:
        D.append(dt.date(a, m, 1))
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)
    return D


def prix_archive(jour, scelles):
    """Télécharge l'archive d'un jour (ou des jours suivants si elle manque) et renvoie (date réelle, {id: prix})."""
    for decalage in range(6):
        d = jour + dt.timedelta(days=decalage)
        if d > dt.date.today():
            return None, {}
        url = f'{BASE}/archive/tcgplayer/prices-{d.isoformat()}.ppmd.7z'
        r = get(url, stream=True)
        if not r:
            continue
        tmp = tempfile.mkdtemp()
        try:
            chemin = os.path.join(tmp, 'a.7z')
            with open(chemin, 'wb') as f:
                for bloc in r.iter_content(1 << 20):
                    f.write(bloc)
            with py7zr.SevenZipFile(chemin, 'r') as z:
                cibles = [n for n in z.getnames() if f'/{CATEGORIE}/' in n and n.endswith('/prices')]
                z.extract(path=tmp, targets=cibles)
            P = {}
            for n in cibles:
                try:
                    with open(os.path.join(tmp, n), encoding='utf-8') as f:
                        res = json.load(f).get('results', [])
                except (OSError, ValueError):
                    continue
                for x in res:
                    pid = str(x.get('productId'))
                    if pid not in scelles or pid in P:
                        continue
                    v = x.get('marketPrice') or x.get('midPrice') or x.get('lowPrice')
                    if v:
                        P[pid] = round(float(v), 2)
            return d.isoformat(), P
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return None, {}


def main():
    aujourdhui = dt.date.today()
    H = {'v': 1, 'maj': '', 'dates': [], 'p': {}}
    if os.path.exists(SORTIE):
        with open(SORTIE, encoding='utf-8') as f:
            H = json.load(f)
    deja = {d[:7] for d in H['dates']}
    manquantes = [j for j in dates_voulues(aujourdhui) if j.isoformat()[:7] not in deja]
    scelles, magasin = produits_scelles()
    print(f'{len(scelles)} produits scellés Pokémon ; mois à ajouter : {[j.isoformat()[:7] for j in manquantes]}')
    for jour in manquantes:
        date, P = prix_archive(jour, scelles)
        if not date:
            print(f'  {jour.isoformat()[:7]} : archive introuvable')
            continue
        H['dates'].append(date)
        for pid, liste in H['p'].items():
            liste.append(P.get(pid))
        n = len(H['dates'])
        for pid, v in P.items():
            if pid not in H['p']:
                H['p'][pid] = [None] * (n - 1) + [v]
        print(f'  {date} : {len(P)} prix')
    # dates dans l'ordre, séries alignées
    ordre = sorted(range(len(H['dates'])), key=lambda i: H['dates'][i])
    H['dates'] = [H['dates'][i] for i in ordre]
    H['p'] = {pid: [l[i] if i < len(l) else None for i in ordre] for pid, l in H['p'].items()}
    # rien avant la sortie : les prix de précommande faussent l'évolution
    H['sortie'] = {pid: scelles[pid] for pid in H['p'] if pid in scelles}
    for pid, liste in H['p'].items():
        s = H['sortie'].get(pid)
        if s is not None:
            H['p'][pid] = [v if (s and d >= s) else None for d, v in zip(H['dates'], liste)]
    H['p'] = {pid: l for pid, l in H['p'].items() if any(l)}
    # prix le jour de la sortie, pour les produits sortis depuis le début des archives
    L = H.get('lancement') or {}
    L = {pid: v for pid, v in L.items() if pid in H['p'] and H['sortie'].get(pid) and v and v[0] >= H['sortie'][pid]}
    a_faire = {}
    for pid in H['p']:
        s = H['sortie'].get(pid)
        if s and s >= DEBUT.isoformat() and s not in H['dates'] and pid not in L:
            a_faire.setdefault(s, set()).add(pid)
    print(f'{len(a_faire)} dates de sortie à relever')
    for s, ids in sorted(a_faire.items()):
        date, P = prix_archive(dt.date.fromisoformat(s), ids)
        for pid in ids:
            if date and pid in P:
                L[pid] = [date, P[pid]]
    H['lancement'] = L
    # prix en magasin à la sortie (estimé), point de départ de la courbe sur le site
    H['magasin'] = {pid: v for pid, v in magasin.items() if pid in H['p']}
    H['maj'] = aujourdhui.isoformat()
    os.makedirs(os.path.dirname(SORTIE), exist_ok=True)
    with open(SORTIE, 'w', encoding='utf-8') as f:
        json.dump(H, f, separators=(',', ':'))
    print(f"Écrit : {len(H['p'])} produits × {len(H['dates'])} mois")
    return 0


if __name__ == '__main__':
    sys.exit(main())
