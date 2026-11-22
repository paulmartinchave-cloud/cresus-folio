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
"""
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import time

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


def produits_scelles():
    """Produits scellés Pokémon du catalogue actuel de TCGCSV : {id: date de sortie 'AAAA-MM-JJ', ou '' si en précommande}."""
    groupes = get(f'{BASE}/tcgplayer/{CATEGORIE}/groups').json()['results']
    ids = {}
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
            ids[str(p['productId'])] = '' if pre.get('isPresale') else ((pre.get('releasedOn') or '')[:10] or sortie_ext)
        time.sleep(0.05)
    return ids


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
    scelles = produits_scelles()
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
    H['maj'] = aujourdhui.isoformat()
    os.makedirs(os.path.dirname(SORTIE), exist_ok=True)
    with open(SORTIE, 'w', encoding='utf-8') as f:
        json.dump(H, f, separators=(',', ':'))
    print(f"Écrit : {len(H['p'])} produits × {len(H['dates'])} mois")
    return 0


if __name__ == '__main__':
    sys.exit(main())
