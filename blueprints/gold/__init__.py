# -*- coding: utf-8 -*-
from flask import Blueprint

gold_bp = Blueprint('gold', __name__, template_folder='templates', static_folder='../../static', static_url_path='/static')

from . import routes  # noqa: E402, F401
