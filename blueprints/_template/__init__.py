# -*- coding: utf-8 -*-
from flask import Blueprint

sector_bp = Blueprint(
    'sector',
    __name__,
    template_folder='templates',
    static_folder='../../static',
    static_url_path='/static',
)

from . import routes  # noqa: E402, F401
