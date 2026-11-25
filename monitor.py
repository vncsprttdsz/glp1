#!/usr/bin/env python3
"""Monitor da Anvisa: processo de GLP-1 manipulados (25351.912958/2026-33).

Verifica, a cada execução:
  1. As pautas das Reuniões da Diretoria Colegiada (Dicol) do ano corrente
     (e do próximo, se a pasta existir): pauta nova, pauta republicada, e se o
     processo entrou ou saiu da pauta.
  2. A página de "Minutas prévias": se o processo aparece, se foi marcado como
     retirado e se o link da minuta (IN/RDC) foi publicado.

Cada mudança relevante vira uma issue no GitHub (que gera e-mail/push).
O estado fica em state.json, commitado pelo workflow.

Usa a API REST do Plone (++api++) do portal gov.br, que devolve JSON com o
conteúdo que o site renderiza via JavaScript.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

PROCESSO = "25351.912958/2026-33"
# Tolerante a quebras de linha/espaços que o PDF possa inserir.
PROC_RE = re.compile(r"912958\s*/\s*2026")
# Termos de apoio: avisam se o tema aparecer sob outro número de processo.
KEYWORDS = re.compile(
    r"GLP-?1|semaglutid|tirzepatid|liraglutid|dulaglutid|agonistas? (do|de) receptor",
    re.I,
)

BASE = "https://www.gov.br/anvisa"
PAUTAS_PATH = "/pt-br/composicao/diretoria-colegiada/reunioes-da-diretoria/pautas"
MINUTAS_PATH = "/pt-br/assuntos/regulamentacao/agenda-regulatoria/minutas-previas"
PAUTAS_URL = BASE + PAUTAS_PATH
MINUTAS_URL = BASE + MINUTAS_PATH

STATE_FILE = os.environ.get("STATE_FILE", "state.json")
HISTORY_FILE = os.environ.get("HISTORY_FILE", "history.md")
MAX_PDFS_PER_RUN = 6        # pautas mais recentes que são (re)lidas
FAIL_ALERT_AFTER = 8        # execuções seguidas com erro antes de avisar
UA = "Mozilla/5.0 (compatible; anvisa-pauta-monitor/1.0)"

TZ_BR = dt.timezone(dt.timedelta(hours=-3))


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def http_get(url: str, accept: str = "*/*", tries: int = 4) -> bytes:
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            last = e
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(3 * (i + 1))
    raise RuntimeError(f"falha ao baixar {url}: {last}")


def api(path: str, query: str = "") -> dict:
    url = f"{BASE}/++api++{path}" + (f"?{query}" if query else "")
    return json.loads(http_get(url, accept="application/json"))


# --------------------------------------------------------------------------- #
# Texto
# --------------------------------------------------------------------------- #
def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


def snippet(text: str, m: re.Match, before: int = 400, after: int = 900) -> str:
    s = text[max(0, m.start() - before): m.end() + after]
    return re.sub(r"\s+", " ", s).strip()


def keyword_hits(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in KEYWORDS.finditer(text)})


def walk_slate(nodes, out_text: list[str], out_links: list[tuple[str, str]]):
    """Extrai texto e links de um valor Slate (Volto)."""
    for n in nodes or []:
        if not isinstance(n, dict):
            continue
        if "text" in n and isinstance(n["text"], str):
            out_text.append(n["text"])
        if n.get("type") == "link":
            url = (n.get("data") or {}).get("url") or ""
            label_parts: list[str] = []
            walk_slate(n.get("children"), label_parts, [])
            out_links.append(("".join(label_parts).strip(), url))
            out_text.extend(label_parts)
            continue
        walk_slate(n.get("children"), out_text, out_links)


def html_to_text(h: str) -> tuple[str, list[tuple[str, str]]]:
    import html as htmlmod

    links = [
        (re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", "", t))).strip(), u)
        for u, t in re.findall(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', h, re.S)
    ]
    h = re.sub(r"<br\s*/?>|</p>|</li>|</h\d>|</tr>|</div>", "\n", h)
    t = htmlmod.unescape(re.sub(r"<[^>]+>", " ", h))
    return re.sub(r"[ \t\xa0]+", " ", t).strip(), links


def abs_url(u: str) -> str:
    if u.startswith("http"):
        return u
    if u.startswith("/anvisa"):
        return "https://www.gov.br" + u
    if u.startswith("/"):
        return BASE + u
    return u


# --------------------------------------------------------------------------- #
# Minutas prévias
# --------------------------------------------------------------------------- #
def read_minutas() -> dict:
    d = api(MINUTAS_PATH)
    lines: list[tuple[str, list[tuple[str, str]]]] = []
    for bid in d.get("blocks_layout", {}).get("items", []):
        b = d["blocks"].get(bid, {})
        t, links = "", []
        if b.get("@type") == "slate":
            parts: list[str] = []
            walk_slate(b.get("value"), parts, links)
            t = "".join(parts).strip() or (b.get("plaintext") or "").strip()
        elif b.get("@type") == "html":
            t, links = html_to_text(b.get("html", ""))
        elif b.get("@type") == "title":
            t = d.get("title", "")
        if t or links:
            lines.append((t, links))

    full = "\n".join(t for t, _ in lines)
    header = ""
    flat = re.sub(r"\s+", " ", full)
    m = re.search(r"Reuni[ãa]o [^.]{0,40}\b(ROP|REP)\b.{0,60}?\d{1,2}/\d{1,2}/\d{4}", flat)
    if m:
        header = m.group(0).strip()

    # Separa em itens ("2.1", "2.2", "3.4.2.1"...) para isolar o do processo.
    items: list[dict] = []
    cur = None
    for t, links in lines:
        if re.fullmatch(r"\d+(\.\d+)+\.?", t.strip()):
            cur = {"num": t.strip().rstrip("."), "text": [], "links": []}
            items.append(cur)
            continue
        if cur is not None:
            cur["text"].append(t)
            cur["links"].extend(links)

    proc_item = None
    for it in items:
        txt = "\n".join(it["text"])
        if PROC_RE.search(txt):
            proc_item = {
                "num": it["num"],
                "text": txt,
                "retirado": bool(re.search(r"retirad[oa] de pauta", txt, re.I)),
                "minuta_links": [
                    {"label": lbl, "url": abs_url(u)}
                    for lbl, u in it["links"]
                    if re.search(r"minuta|\.pdf", lbl + u, re.I)
                ],
            }
            break

    # Fallback: processo citado fora da estrutura de itens.
    in_page = bool(PROC_RE.search(full))
    all_links = sorted({abs_url(u) for _, ls in lines for _, u in ls if "minutas-previas" in u})
    return {
        "modified": d.get("modified"),
        "header": header,
        "process_present": in_page,
        "process_item": proc_item,
        "keywords": keyword_hits(full),
        "minuta_links_all": all_links,
        "text_hash": hashlib.sha256(full.encode()).hexdigest(),
    }


# --------------------------------------------------------------------------- #
# Pautas
# --------------------------------------------------------------------------- #
def list_pautas(year: int) -> list[dict]:
    try:
        d = api(
            f"{PAUTAS_PATH}/{year}",
            "metadata_fields=modified&metadata_fields=created&b_size=200",
        )
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise
    out = []
    for it in d.get("items", []):
        if it.get("@type") not in ("File", "Document", "Link"):
            continue
        out.append(
            {
                "id": it["@id"],
                "title": it.get("title", ""),
                "modified": it.get("modified"),
                "created": it.get("created"),
                "type": it.get("@type"),
            }
        )
    return out


def pauta_text(item: dict) -> str:
    url = item["id"]
    if item["type"] == "File":
        data = http_get(url + "/@@download/file")
        if data[:4] == b"%PDF":
            return pdf_text(data)
        return data.decode("utf-8", "replace")
    # Página (Document): extrai blocos como na página de minutas.
    d = api(url.replace("https://www.gov.br/anvisa", ""))
    parts: list[str] = []
    for bid in d.get("blocks_layout", {}).get("items", []):
        b = d["blocks"].get(bid, {})
        if b.get("@type") == "slate":
            walk_slate(b.get("value"), parts, [])
            parts.append("\n")
        elif b.get("@type") == "html":
            parts.append(html_to_text(b.get("html", ""))[0] + "\n")
        elif b.get("@type") == "listing":
            pass
    # Se a página só lista um PDF filho, lê o PDF.
    for child in d.get("items", []):
        if child.get("@type") == "File":
            try:
                parts.append(pdf_text(http_get(child["@id"] + "/@@download/file")))
            except Exception:  # noqa: BLE001
                pass
    return "".join(parts)


def analyse_pauta(item: dict) -> dict:
    text = pauta_text(item)
    m = PROC_RE.search(text)
    item_num = None
    snip = None
    if m:
        snip = snippet(text, m)
        # número do item imediatamente anterior ao processo
        nums = re.findall(r"(?m)^\s*(\d+(?:\.\d+)+)\s*$", text[: m.start()])
        item_num = nums[-1] if nums else None
    obs = re.search(r"OBSERVA[ÇC][ÃA]O:(.{0,600})", text, re.S)
    return {
        "title": item["title"],
        "modified": item["modified"],
        "sha": hashlib.sha256(text.encode()).hexdigest(),
        "process_present": bool(m),
        "item_num": item_num,
        "snippet": snip,
        "keywords": keyword_hits(text),
        "observacao": re.sub(r"\s+", " ", obs.group(1)).strip()[:600] if obs else None,
    }


# --------------------------------------------------------------------------- #
# Notificação
# --------------------------------------------------------------------------- #
def gh_issue(title: str, body: str, labels: list[str]) -> None:
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    mention = os.environ.get("NOTIFY_MENTION", "").strip()
    if mention:
        body += f"\n\n@{mention}"
    if not token or not repo:
        print(f"\n=== [ISSUE simulada] {title}\n{body}\n")
        return
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/issues",
        data=json.dumps({"title": title, "body": body, "labels": labels}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": UA,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        print("issue criada:", json.loads(r.read()).get("html_url"))


def now_br() -> str:
    return dt.datetime.now(TZ_BR).strftime("%d/%m/%Y %H:%M")


# --------------------------------------------------------------------------- #
# Principal
# --------------------------------------------------------------------------- #
def load_state() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(st: dict) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def log_history(lines: list[str]) -> None:
    new = not os.path.exists(HISTORY_FILE)
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        if new:
            f.write(f"# Histórico do monitor – processo {PROCESSO}\n\n")
        for ln in lines:
            f.write(f"- {now_br()} — {ln}\n")


def run() -> int:
    st = load_state()
    first_run = not st
    events: list[tuple[str, str, list[str]]] = []  # (title, body, labels)
    hist: list[str] = []

    # ---------------- Minutas prévias ----------------
    mi = read_minutas()
    old = st.get("minutas") or {}
    oi = old.get("process_item") or {}
    ni = mi.get("process_item") or {}
    old_present = bool(old.get("process_present"))
    new_present = bool(mi["process_present"])
    old_links = {l["url"] for l in oi.get("minuta_links", [])}
    new_links = [l for l in ni.get("minuta_links", []) if l["url"] not in old_links]

    def mi_body(extra: str = "") -> str:
        b = [f"**Página:** {MINUTAS_URL}", f"**Reunião indicada na página:** {mi['header'] or '—'}"]
        if ni:
            b.append(f"**Item:** {ni['num']}" + (" — **RETIRADO DE PAUTA**" if ni["retirado"] else ""))
            b.append("```\n" + ni["text"][:2500] + "\n```")
            for l in ni.get("minuta_links", []):
                b.append(f"- [{l['label'] or 'minuta'}]({l['url']})")
        if extra:
            b.append(extra)
        return "\n\n".join(b)

    if not first_run:
        if new_present and not old_present:
            events.append((f"🔔 Processo {PROCESSO} apareceu em Minutas prévias", mi_body(), ["minuta", "pauta"]))
            hist.append(f"processo apareceu na página de minutas ({mi['header']})")
        elif old_present and not new_present:
            events.append(
                (f"⚠️ Processo {PROCESSO} saiu da página de Minutas prévias",
                 mi_body("O processo não aparece mais na página — possível retirada de pauta."),
                 ["retirado"]))
            hist.append("processo saiu da página de minutas")
        if ni and ni.get("retirado") and not oi.get("retirado"):
            events.append((f"⚠️ Processo {PROCESSO} marcado como RETIRADO DE PAUTA (minutas)", mi_body(), ["retirado"]))
            hist.append("item do processo marcado como retirado na página de minutas")
        if new_links:
            lst = "\n".join(f"- [{l['label'] or 'minuta'}]({l['url']})" for l in new_links)
            events.append((f"📄 Minuta publicada para o processo {PROCESSO}", mi_body("**Novos links:**\n" + lst), ["minuta"]))
            hist.append("minuta publicada: " + ", ".join(l["url"] for l in new_links))
        new_kw = sorted(set(mi["keywords"]) - set(old.get("keywords", [])))
        if new_kw and not new_present:
            events.append(
                (f"🔎 Termos de GLP-1 apareceram em Minutas prévias ({', '.join(new_kw)})",
                 mi_body("Os termos apareceram, mas não com o número do processo monitorado. Confira."),
                 ["possivel"]))
            hist.append(f"termos {new_kw} na página de minutas")
        if mi["header"] and mi["header"] != old.get("header"):
            hist.append(f"página de minutas agora refere-se a: {mi['header']}")
            status = ("✅ o processo ESTÁ na lista" if new_present
                      else "❌ o processo **não** está na lista")
            events.append(
                (f"🗓️ Minutas prévias atualizadas para nova reunião: {mi['header']}",
                 mi_body(f"A página passou a listar as propostas de outra reunião — {status}."),
                 ["minuta"]))
    st["minutas"] = mi

    # ---------------- Pautas ----------------
    year = dt.datetime.now(TZ_BR).year
    listing: list[dict] = []
    for y in (year, year + 1):
        listing.extend(list_pautas(y))
    listing.sort(key=lambda x: x.get("created") or "", reverse=True)

    known: dict = st.setdefault("pautas", {})
    for idx, item in enumerate(listing):
        prev = known.get(item["id"])
        is_new = prev is None
        changed = prev is not None and prev.get("modified") != item["modified"]
        if idx >= MAX_PDFS_PER_RUN:
            if is_new:
                known[item["id"]] = {"title": item["title"], "modified": item["modified"], "skipped": True}
            continue
        if not (is_new or changed or prev.get("skipped")):
            continue
        try:
            a = analyse_pauta(item)
        except Exception as e:  # noqa: BLE001
            print(f"erro lendo pauta {item['id']}: {e}", file=sys.stderr)
            continue
        if prev and prev.get("sha") == a["sha"] and not is_new:
            known[item["id"]] = a
            continue
        known[item["id"]] = a
        if first_run or (prev and prev.get("skipped")):
            continue

        link = item["id"]
        status = "✅ **O processo ESTÁ na pauta**" if a["process_present"] else "❌ O processo **não** está na pauta"
        body = [f"**Pauta:** [{a['title']}]({link})", f"**Página:** {PAUTAS_URL}", status]
        if a["item_num"]:
            body.append(f"**Item:** {a['item_num']}")
        if a["snippet"]:
            body.append("```\n" + a["snippet"][:2500] + "\n```")
        if a["observacao"]:
            body.append(f"**Observação da pauta:** {a['observacao']}")
        if a["keywords"] and not a["process_present"]:
            body.append(f"Termos relacionados encontrados no PDF: {', '.join(a['keywords'])}")
        body_s = "\n\n".join(body)

        was_present = bool(prev and prev.get("process_present"))
        if is_new:
            if a["process_present"]:
                events.append((f"🔔 Processo {PROCESSO} ENTROU na pauta: {a['title']}", body_s, ["pauta"]))
            else:
                events.append((f"🗓️ Nova pauta publicada (sem o processo): {a['title']}", body_s, ["nova-pauta"]))
            hist.append(f"nova pauta: {a['title']} — processo {'PRESENTE' if a['process_present'] else 'ausente'}")
        else:
            if a["process_present"] and not was_present:
                events.append((f"🔔 Processo {PROCESSO} INCLUÍDO na pauta republicada: {a['title']}", body_s, ["pauta"]))
            elif was_present and not a["process_present"]:
                events.append((f"⚠️ Processo {PROCESSO} RETIRADO da pauta: {a['title']}", body_s, ["retirado"]))
            else:
                events.append((f"🔁 Pauta republicada: {a['title']}", body_s, ["nova-pauta"]))
            hist.append(f"pauta alterada: {a['title']} — processo {'PRESENTE' if a['process_present'] else 'ausente'}")

    # (sem timestamp de "última execução" para não gerar commit a cada rodada)
    for k in ("fail_count", "fail_alerted", "last_error"):
        st.pop(k, None)

    if first_run:
        hist.append(
            "monitor iniciado. Processo na página de minutas: "
            + ("sim" if mi["process_present"] else "não")
            + f"; reunião indicada: {mi['header'] or '—'}; pautas conhecidas: {len(known)}"
        )
        present_in = [v["title"] for v in known.values() if v.get("process_present")]
        events.append(
            (f"✅ Monitor da Anvisa ativo – processo {PROCESSO}",
             "Linha de base registrada.\n\n"
             f"- Página de minutas: {MINUTAS_URL}\n  - Reunião indicada: {mi['header'] or '—'}\n"
             f"  - Processo presente: {'sim' if mi['process_present'] else 'não'}\n"
             f"- Pautas ({PAUTAS_URL}): {len(known)} conhecidas; processo presente em: "
             f"{', '.join(present_in) or 'nenhuma das mais recentes'}\n\n"
             "Você receberá uma issue quando: uma nova pauta for publicada ou republicada; o processo entrar ou sair "
             "de uma pauta; o processo aparecer, sumir ou for marcado como retirado na página de minutas; o link da "
             "minuta for publicado.",
             ["status"]))

    for title, body, labels in events:
        try:
            gh_issue(title, body + f"\n\n_Detectado em {now_br()} (horário de Brasília)._", labels)
        except Exception as e:  # noqa: BLE001
            print(f"falha ao criar issue: {e}", file=sys.stderr)
            print(title, body)
    if hist:
        log_history(hist)
    save_state(st)
    print(f"ok: {len(events)} evento(s). minutas: processo={'sim' if mi['process_present'] else 'não'} "
          f"({mi['header']}); pautas analisadas: {len(listing)}")
    return 0


def main() -> int:
    try:
        return run()
    except Exception as e:  # noqa: BLE001
        st = load_state()
        st["fail_count"] = st.get("fail_count", 0) + 1
        st["last_error"] = f"{dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}: {e}"
        print("ERRO:", e, file=sys.stderr)
        if st["fail_count"] >= FAIL_ALERT_AFTER and not st.get("fail_alerted"):
            try:
                gh_issue(
                    "🛑 Monitor da Anvisa falhando repetidamente",
                    f"{st['fail_count']} execuções seguidas falharam. Último erro:\n\n```\n{e}\n```\n\n"
                    "Enquanto isso durar, mudanças no site podem não ser detectadas.",
                    ["erro"],
                )
                st["fail_alerted"] = True
            except Exception as e2:  # noqa: BLE001
                print("falha ao criar issue de erro:", e2, file=sys.stderr)
        if st.get("minutas") or st.get("pautas"):
            save_state(st)
        return 0  # falha tratada: não marca o workflow como falho (evita e-mails a cada 15 min)


if __name__ == "__main__":
    sys.exit(main())
