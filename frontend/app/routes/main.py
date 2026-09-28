from app.utils.api_client import ApiClient

from flask import Blueprint,render_template

from app.utils.decorators import login_required, permission_required

main_bp = Blueprint('main', __name__)

@main_bp.route('/index', methods=["GET", "POST"])
@login_required
def index():
    api = ApiClient()
    tip_data = None
    try:
        tip_response = api.get("/tips")
        if tip_response.status_code == 200:
            tip_data = tip_response.json()
    except Exception:
        pass
    return render_template("index.html", tip=tip_data)


@main_bp.route('/api/tips/next', methods=["GET"])
@login_required
def get_next_tip():
    """Endpoint AJAX para rotar bajo demanda al siguiente tip disponible."""
    api = ApiClient()
    try:
        tip_resp = api.get("/tips?aleatorio=true")
        if tip_resp.status_code == 200:
            return tip_resp.json()
    except Exception:
        pass
    return {"tips": None}


@main_bp.route('/api/tips/<int:tip_id>/feedback', methods=["POST"])
@login_required
def toggle_tip_feedback(tip_id: int):
    """Endpoint AJAX para registrar o alternar feedback ('Me sirvió') sobre un tip."""
    api = ApiClient()
    try:
        resp = api.post(f"/tips/items/{tip_id}/feedback")
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return {"ok": False, "likes_count": 0, "user_voted": False}



