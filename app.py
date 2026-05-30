from __future__ import annotations
import html
import json
import random
import re
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

ROOT = Path(__file__).parent
DATA_FILE = ROOT / "data.json"
STYLE_FILE = ROOT / "styles.css"
DAY = 24 * 60 * 60

def make_card(term: str = "", definition: str = "") -> dict:
    return {
        "id": str(uuid.uuid4()),
        "term": term,
        "definition": definition,
        "interval": 0,
        "ease": 2.5,
        "due": int(time.time()),
        "correct": 0,
        "attempts": 0,
    }


def sample_data() -> dict:
    first_set = {
        "id": str(uuid.uuid4()),
        "title": "Xin chào",
        "subject": "Tiếng Anh",
        "notes": "Xin chào",
        "cards": [
            make_card("Hello World", "Chào thế giới"),
        ],
    }
    return {"active_set_id": first_set["id"], "sets": [first_set]}


def load_data() -> dict:
    if not DATA_FILE.exists():
        data = sample_data()
        save_data(data)
        return data
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        data = sample_data()
    if not data.get("sets"):
        data = sample_data()
    return data


def save_data(data: dict) -> None:
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def esc(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", value.lower())).strip()


def close_answer(value: str, answer: str) -> bool:
    cleaned_value = normalize(value)
    cleaned_answer = normalize(answer)
    return bool(cleaned_value and (cleaned_value in cleaned_answer or cleaned_answer in cleaned_value))


def first(values: dict[str, list[str]], key: str, default: str = "") -> str:
    items = values.get(key)
    return items[0] if items else default


def find_set(data: dict, set_id: str | None) -> dict:
    for study_set in data["sets"]:
        if study_set["id"] == set_id:
            return study_set
    return data["sets"][0]


def find_card(study_set: dict, card_id: str | None) -> dict | None:
    for card in study_set["cards"]:
        if card["id"] == card_id:
            return card
    return None


def due_cards(study_set: dict) -> list[dict]:
    now = int(time.time())
    return [card for card in study_set["cards"] if int(card.get("due", 0)) <= now]


def app_url(mode: str, set_id: str, **params: object) -> str:
    query = {"mode": mode, "set": set_id}
    query.update({key: value for key, value in params.items() if value is not None})
    return f"/?{urlencode(query)}"


def redirect(handler: BaseHTTPRequestHandler, target: str) -> None:
    handler.send_response(HTTPStatus.SEE_OTHER)
    handler.send_header("Location", target)
    handler.end_headers()


def parse_body(handler: BaseHTTPRequestHandler) -> dict[str, list[str]]:
    length = int(handler.headers.get("Content-Length", 0))
    raw = handler.rfile.read(length).decode("utf-8")
    return parse_qs(raw, keep_blank_values=True)


class StudyHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/styles.css":
            self.serve_css()
            return
        if parsed.path == "/export":
            self.export_set(parsed.query)
            return
        if parsed.path != "/":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        data = load_data()
        query = parse_qs(parsed.query)
        study_set = find_set(data, first(query, "set", data.get("active_set_id", "")))
        mode = first(query, "mode", "cards")
        body = render_page(data, study_set, mode, query)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/action":
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        data = load_data()
        form = parse_body(self)
        action = first(form, "action")
        study_set = find_set(data, first(form, "set_id", data.get("active_set_id", "")))

        if action == "new_set":
            study_set = {
                "id": str(uuid.uuid4()),
                "title": "Bộ học mới",
                "subject": "",
                "notes": "",
                "cards": [make_card("Thuật ngữ mới", "Định nghĩa mới")],
            }
            data["sets"].insert(0, study_set)
            data["active_set_id"] = study_set["id"]
            save_data(data)
            redirect(self, app_url("edit", study_set["id"]))
            return

        if action == "delete_set":
            if len(data["sets"]) > 1:
                data["sets"] = [item for item in data["sets"] if item["id"] != study_set["id"]]
                data["active_set_id"] = data["sets"][0]["id"]
            save_data(data)
            redirect(self, app_url("cards", data["active_set_id"]))
            return

        if action == "save_set":
            save_set(study_set, form)
            save_data(data)
            redirect(self, app_url("edit", study_set["id"], saved="1"))
            return

        if action == "add_blank_card":
            study_set["cards"].append(make_card("Thuật ngữ mới", "Định nghĩa mới"))
            save_data(data)
            redirect(self, app_url("edit", study_set["id"]))
            return

        if action == "extract_notes":
            added = extract_notes(study_set, first(form, "notes"))
            save_data(data)
            redirect(self, app_url("notes", study_set["id"], added=added))
            return

        if action == "learn":
            card = find_card(study_set, first(form, "card_id"))
            if card:
                is_correct = close_answer(first(form, "answer"), card["definition"])
                card["attempts"] = int(card.get("attempts", 0)) + 1
                card["correct"] = int(card.get("correct", 0)) + int(is_correct)
                save_data(data)
                feedback = "correct" if is_correct else "wrong"
                redirect(self, app_url("learn", study_set["id"], feedback=feedback, expected=card["definition"]))
                return

        if action == "rate_review":
            rate_review(study_set, first(form, "card_id"), first(form, "rating"))
            save_data(data)
            redirect(self, app_url("review", study_set["id"]))
            return

        if action == "grade_test":
            score, total = grade_test(study_set, form)
            redirect(self, app_url("test", study_set["id"], score=f"{score}/{total}"))
            return

        redirect(self, app_url("cards", study_set["id"]))

    def serve_css(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/css; charset=utf-8")
        self.end_headers()
        self.wfile.write(STYLE_FILE.read_bytes())

    def export_set(self, query_string: str) -> None:
        data = load_data()
        query = parse_qs(query_string)
        study_set = find_set(data, first(query, "set", data.get("active_set_id", "")))
        payload = json.dumps(study_set, ensure_ascii=False, indent=2).encode("utf-8")
        filename = re.sub(r"[^a-zA-Z0-9_-]+", "-", study_set.get("title", "study-set")).strip("-") or "study-set"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}.json"')
        self.end_headers()
        self.wfile.write(payload)


def save_set(study_set: dict, form: dict[str, list[str]]) -> None:
    study_set["title"] = first(form, "title", "Chưa đặt tên").strip() or "Chưa đặt tên"
    study_set["subject"] = first(form, "subject").strip()
    study_set["notes"] = first(form, "notes")
    existing = {card["id"]: card for card in study_set["cards"]}
    cards = []
    for index, term in enumerate(form.get("term", [])):
        definition = form.get("definition", [""])[index] if index < len(form.get("definition", [])) else ""
        card_id = form.get("card_id", [str(uuid.uuid4())])[index] if index < len(form.get("card_id", [])) else str(uuid.uuid4())
        if not term.strip() or not definition.strip():
            continue
        card = existing.get(card_id, make_card())
        card.update({"id": card_id, "term": term.strip(), "definition": definition.strip()})
        cards.append(card)
    study_set["cards"] = cards


def extract_notes(study_set: dict, notes: str) -> int:
    study_set["notes"] = notes
    added = 0
    known_terms = {normalize(card["term"]) for card in study_set["cards"]}
    for line in notes.splitlines():
        match = re.match(r"^(.+?)\s*(?:-|:|=)\s*(.+)$", line.strip())
        if not match:
            continue
        term, definition = match.group(1).strip(), match.group(2).strip()
        if term and definition and normalize(term) not in known_terms:
            study_set["cards"].append(make_card(term, definition))
            known_terms.add(normalize(term))
            added += 1
    return added


def rate_review(study_set: dict, card_id: str, rating: str) -> None:
    card = find_card(study_set, card_id)
    if not card:
        return
    interval = int(card.get("interval", 0))
    ease = float(card.get("ease", 2.5))
    if rating == "again":
        interval, ease = 0, max(1.3, ease - 0.2)
    elif rating == "hard":
        interval, ease = max(1, round(interval * 1.2)), max(1.3, ease - 0.05)
    elif rating == "easy":
        interval, ease = max(3, round((interval or 1) * (ease + 0.6))), ease + 0.15
    else:
        interval = max(1, round((interval or 1) * ease))
    card["interval"] = interval
    card["ease"] = ease
    card["due"] = int(time.time() + interval * DAY)
    card["attempts"] = int(card.get("attempts", 0)) + 1
    card["correct"] = int(card.get("correct", 0)) + int(rating != "again")


def grade_test(study_set: dict, form: dict[str, list[str]]) -> tuple[int, int]:
    score = 0
    card_ids = form.get("card_id", [])
    for card_id in card_ids:
        card = find_card(study_set, card_id)
        if card and close_answer(first(form, f"answer_{card_id}"), card["definition"]):
            score += 1
    return score, len(card_ids)


def render_page(data: dict, study_set: dict, mode: str, query: dict[str, list[str]]) -> str:
    mode = mode if mode in {"cards", "learn", "test", "review", "notes", "edit"} else "cards"
    cards = study_set["cards"]
    attempts = sum(int(card.get("attempts", 0)) for card in cards)
    correct = sum(int(card.get("correct", 0)) for card in cards)
    accuracy = f"{round(correct / attempts * 100)}%" if attempts else "0%"
    return f"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Kaizu</title>
  <link rel="stylesheet" href="/styles.css" />
</head>
<body>
  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand"><div class="brand-mark">KZ</div><div><h1>Kaizu</h1></div></div>
      <form method="post" action="/action"><input type="hidden" name="action" value="new_set" /><button class="primary-action" type="submit">+ Bộ học mới</button></form>
      <div class="set-list-header"><span>Bộ học</span><span>{len(data["sets"])}</span></div>
      <div class="set-list">{render_set_list(data, study_set, mode)}</div>
    </aside>
    <main class="workspace">
      <header class="topbar">
        <div><p class="eyebrow">Bộ đang học</p><h2>{esc(study_set["title"])}</h2></div>
        <div class="topbar-actions">
          <a class="ghost-btn link-btn" href="/export?set={esc(study_set["id"])}">Xuất</a>
          <form method="post" action="/action"><input type="hidden" name="action" value="delete_set" /><input type="hidden" name="set_id" value="{esc(study_set["id"])}" /><button class="danger-btn" type="submit">Xóa</button></form>
        </div>
      </header>
      <nav class="mode-tabs">{render_tabs(mode, study_set["id"])}</nav>
      <section class="stats-strip">
        <div><span>{len(cards)}</span><p>Thẻ</p></div>
        <div><span>{len(due_cards(study_set))}</span><p>Đến hạn</p></div>
        <div><span>{sum(1 for card in cards if int(card.get("interval", 0)) >= 7)}</span><p>Đã nhớ</p></div>
        <div><span>{accuracy}</span><p>Độ chính xác</p></div>
      </section>
      {render_panel(study_set, mode, query)}
    </main>
  </div>
</body>
</html>"""


def render_set_list(data: dict, active_set: dict, mode: str) -> str:
    html_items = []
    for study_set in data["sets"]:
        active = " active" if study_set["id"] == active_set["id"] else ""
        html_items.append(
            f'<a class="set-item{active}" href="{app_url(mode, study_set["id"])}"><strong>{esc(study_set["title"])}</strong>'
            f'<span>{len(study_set["cards"])} thẻ · {esc(study_set.get("subject") or "Chưa phân loại")}</span></a>'
        )
    return "".join(html_items)


def render_tabs(active_mode: str, set_id: str) -> str:
    tabs = [("cards", "Thẻ"), ("learn", "Học"), ("test", "Kiểm tra"), ("review", "Ôn tập"), ("notes", "Ghi chú"), ("edit", "Chỉnh sửa")]
    return "".join(f'<a class="tab{" active" if mode == active_mode else ""}" href="{app_url(mode, set_id)}">{label}</a>' for mode, label in tabs)


def render_panel(study_set: dict, mode: str, query: dict[str, list[str]]) -> str:
    return {
        "cards": render_cards,
        "learn": render_learn,
        "test": render_test,
        "review": render_review,
        "notes": render_notes,
        "edit": render_edit,
    }[mode](study_set, query)


def render_cards(study_set: dict, query: dict[str, list[str]]) -> str:
    cards = study_set["cards"]
    index = max(0, min(int(first(query, "card", "0") or 0), max(len(cards) - 1, 0)))
    card = cards[index] if cards else None
    cards_json = json.dumps([{"term": c["term"], "definition": c["definition"]} for c in cards], ensure_ascii=False)
    return f"""<section class="panel active"><div class="study-stage">
      <div class="flashcard-scene" onclick="flipCard()">
        <div class="flashcard" id="flashcard">
          <div class="flashcard-face flashcard-front"><p>Thuật ngữ</p><strong>{esc(card["term"] if card else "Chưa có thẻ")}</strong></div>
          <div class="flashcard-face flashcard-back"><p>Định nghĩa</p><strong>{esc(card["definition"] if card else "")}</strong></div>
        </div>
      </div>
      <div class="card-controls"><a class="icon-btn link-btn" href="javascript:prevCard()">‹</a><span id="cardCounter">{index + 1 if cards else 0} / {len(cards)}</span><a class="icon-btn link-btn" href="javascript:nextCard()">›</a></div>
    </div>
    <script>
    const allCards = {cards_json};
    let currentCard = {index};
    function flipCard() {{
      const el = document.getElementById('flashcard');
      el.classList.toggle('flipped');
    }}
    function showCard(i) {{
      if (!allCards.length) return;
      currentCard = ((i % allCards.length) + allCards.length) % allCards.length;
      const card = allCards[currentCard];
      const el = document.getElementById('flashcard');
      el.classList.remove('flipped');
      setTimeout(() => {{
        el.querySelector('.flashcard-front strong').textContent = card.term;
        el.querySelector('.flashcard-back strong').textContent = card.definition;
        document.getElementById('cardCounter').textContent = (currentCard + 1) + ' / ' + allCards.length;
      }}, 150);
    }}
    function prevCard() {{ showCard(currentCard - 1); }}
    function nextCard() {{ showCard(currentCard + 1); }}
    </script>
    </section>"""


def render_learn(study_set: dict, query: dict[str, list[str]]) -> str:
    card = random.choice(study_set["cards"]) if study_set["cards"] else None
    feedback = first(query, "feedback")
    message = "Đúng." if feedback == "correct" else f"Chưa đúng. Đáp án: {esc(first(query, 'expected'))}" if feedback == "wrong" else ""
    return f"""<section class="panel active"><div class="task-layout">
      <div class="prompt-box"><p>Trả lời định nghĩa cho thuật ngữ</p><h3>{esc(card["term"] if card else "Chưa có câu hỏi")}</h3></div>
      <form class="answer-form" method="post" action="/action">
        <input type="hidden" name="action" value="learn" /><input type="hidden" name="set_id" value="{esc(study_set["id"])}" /><input type="hidden" name="card_id" value="{esc(card["id"] if card else "")}" />
        <input name="answer" autocomplete="off" placeholder="Nhập câu trả lời" /><button class="primary-action compact" type="submit">Kiểm tra</button>
      </form><div class="feedback">{message}</div>
    </div></section>"""


def render_test(study_set: dict, query: dict[str, list[str]]) -> str:
    cards = random.sample(study_set["cards"], min(8, len(study_set["cards"])))
    questions = []
    for index, card in enumerate(cards, start=1):
        questions.append(f"""<div class="question"><label><strong>{index}. {esc(card["term"])}</strong><input name="answer_{esc(card["id"])}" placeholder="Nhập định nghĩa" /></label><input type="hidden" name="card_id" value="{esc(card["id"])}" /></div>""")
    body = "".join(questions) or "<p>Chưa có thẻ để tạo bài kiểm tra.</p>"
    return f"""<section class="panel active"><div class="test-toolbar"><span id="testScore">{esc(first(query, "score"))}</span></div>
      <form class="test-list" method="post" action="/action"><input type="hidden" name="action" value="grade_test" /><input type="hidden" name="set_id" value="{esc(study_set["id"])}" />{body}<button class="primary-action compact" type="submit">Chấm điểm</button></form>
    </section>"""


def render_review(study_set: dict, query: dict[str, list[str]]) -> str:
    card = due_cards(study_set)[0] if due_cards(study_set) else None
    answer = esc(card["definition"]) if card and first(query, "show") == "1" else ""
    buttons = "".join(f'<button name="rating" value="{rating}" type="submit">{label}</button>' for rating, label in [("again", "Làm lại"), ("hard", "Khó"), ("good", "Bình thường"), ("easy", "Dễ")]) if card else ""
    return f"""<section class="panel active"><div class="task-layout">
      <div class="prompt-box"><p>Lặp lại cách quãng</p><h3>{esc(card["term"] if card else "Không có thẻ đến hạn")}</h3></div>
      <a class="ghost-btn wide link-btn" href="{app_url("review", study_set["id"], show=1)}">Hiện đáp án</a><div class="review-answer">{answer}</div>
      <form class="rating-row" method="post" action="/action"><input type="hidden" name="action" value="rate_review" /><input type="hidden" name="set_id" value="{esc(study_set["id"])}" /><input type="hidden" name="card_id" value="{esc(card["id"] if card else "")}" />{buttons}</form>
    </div></section>"""


def render_notes(study_set: dict, query: dict[str, list[str]]) -> str:
    added = first(query, "added")
    message = f"Đã tạo {esc(added)} thẻ mới." if added else ""
    return f"""<section class="panel active"><div class="notes-grid">
      <form class="notes-editor" method="post" action="/action"><input type="hidden" name="action" value="extract_notes" /><input type="hidden" name="set_id" value="{esc(study_set["id"])}" />
        <label for="notesInput">Ghi chú</label><textarea id="notesInput" name="notes" placeholder="Photosynthesis - Quá trình cây tạo glucose từ ánh sáng">{esc(study_set.get("notes", ""))}</textarea>
        <button class="primary-action compact" type="submit">Tạo thẻ từ ghi chú</button><div class="feedback">{message}</div>
      </form><div class="notes-tips"><h3>Định dạng nhận diện</h3><p>Mỗi dòng dùng một mẫu: thuật ngữ - định nghĩa, thuật ngữ: định nghĩa, hoặc thuật ngữ = định nghĩa.</p></div>
    </div></section>"""


def render_edit(study_set: dict, query: dict[str, list[str]]) -> str:
    rows = "".join(f"""<div class="term-row"><input type="hidden" name="card_id" value="{esc(card["id"])}" /><input name="term" placeholder="Thuật ngữ" value="{esc(card["term"])}" /><input name="definition" placeholder="Định nghĩa" value="{esc(card["definition"])}" /><span></span></div>""" for card in study_set["cards"])
    saved = "Đã lưu thay đổi." if first(query, "saved") else ""
    return f"""<section class="panel active">
      <form class="set-form" method="post" action="/action"><input type="hidden" name="action" value="save_set" /><input type="hidden" name="set_id" value="{esc(study_set["id"])}" />
        <div class="field-row"><label>Tên bộ học<input name="title" value="{esc(study_set["title"])}" /></label><label>Môn học<input name="subject" value="{esc(study_set.get("subject", ""))}" /></label></div>
        <label>Ghi chú<textarea name="notes">{esc(study_set.get("notes", ""))}</textarea></label><div class="terms-header"><h3>Thuật ngữ</h3></div><div class="terms-table">{rows}</div>
        <button class="primary-action compact" type="submit">Lưu thay đổi</button><div class="feedback">{saved}</div>
      </form>
      <form class="set-form add-card-form" method="post" action="/action"><input type="hidden" name="action" value="add_blank_card" /><input type="hidden" name="set_id" value="{esc(study_set["id"])}" /><button class="ghost-btn" type="submit">+ Thẻ</button></form>
    </section>"""

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8080))
    server = ThreadingHTTPServer(("0.0.0.0", port), StudyHandler)
    print(f"Máy chủ đang chạy tại http://0.0.0.0:{port}")
    server.serve_forever()
