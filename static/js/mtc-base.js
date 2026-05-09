const MTC = (function() {
  const _config = {
    apiPrefix: '',
    loginPath: 'api/login',
    logoutPath: 'api/logout',
    checkAuthPath: 'api/check_auth',
    changePasswordPath: '/api/change_password',
    onAuthChange: null,
    onLoginSuccess: null,
  };

  let _isLoggedIn = false;
  let _csrfToken = '';

  const _apiCache = {};
  const _inflightRequests = {};

  function configure(opts) {
    Object.assign(_config, opts);
  }

  function isLoggedIn() {
    return _isLoggedIn;
  }

  function getCsrfToken() {
    return _csrfToken;
  }

  function showToast(msg, type) {
    type = type || 'success';
    const t = document.getElementById('toast');
    if (!t) return;
    t.textContent = msg;
    t.className = 'toast ' + type + ' show';
    setTimeout(function() { t.classList.remove('show'); }, 3000);
  }

  function resolveUrl(url) {
    if (/^https?:\/\//i.test(url)) return url;
    if (url.charAt(0) === '/') return url;
    if (!_config.apiPrefix) return '/' + url;
    return _config.apiPrefix + '/' + url;
  }

  async function api(url, opts) {
    opts = opts || {};
    const headers = {'Content-Type': 'application/json'};
    if (_csrfToken && opts.method && opts.method !== 'GET') {
      headers['X-CSRF-Token'] = _csrfToken;
    }
    const body = opts.body === undefined
      ? undefined
      : (typeof opts.body === 'string' ? opts.body : JSON.stringify(opts.body));
    let res;
    try {
      res = await fetch(resolveUrl(url), {
        headers: headers,
        ...opts,
        body: body,
      });
    } catch(e) {
      showToast('网络连接失败', 'error');
      throw e;
    }
    if (res.status === 401) {
      _isLoggedIn = false;
      _csrfToken = '';
      if (_config.onAuthChange) _config.onAuthChange(false);
    }
    return res;
  }

  async function apiCached(url, ttl) {
    ttl = ttl || 60000;
    const cacheKey = resolveUrl(url);
    const now = Date.now();
    if (_apiCache[cacheKey] && now - _apiCache[cacheKey].ts < ttl) {
      return _apiCache[cacheKey].res.clone();
    }
    if (_inflightRequests[cacheKey]) {
      return _inflightRequests[cacheKey].then(function(r) { return r.clone(); });
    }
    const promise = api(url).then(function(res) {
      delete _inflightRequests[cacheKey];
      if (res.ok) {
        _apiCache[cacheKey] = { res: res.clone(), ts: Date.now() };
      }
      return res;
    }).catch(function(e) {
      delete _inflightRequests[cacheKey];
      throw e;
    });
    _inflightRequests[cacheKey] = promise;
    return promise;
  }

  function invalidateCache(urlPattern) {
    if (!urlPattern) {
      Object.keys(_apiCache).forEach(function(k) { delete _apiCache[k]; });
      return;
    }
    Object.keys(_apiCache).forEach(function(k) {
      if (k.indexOf(urlPattern) !== -1) delete _apiCache[k];
    });
  }

  function setCacheEntry(url, data, ttl) {
    ttl = ttl || 60000;
    const cacheKey = resolveUrl(url);
    _apiCache[cacheKey] = {
      res: new Response(JSON.stringify(data), {
        status: 200,
        headers: { 'Content-Type': 'application/json' }
      }),
      ts: Date.now()
    };
  }

  async function checkAuth() {
    try {
      const res = await api(_config.checkAuthPath);
      if (!res.ok) { _isLoggedIn = false; return; }
      const data = await res.json();
      _isLoggedIn = data.logged_in;
      if (data.csrf_token) _csrfToken = data.csrf_token;
      if (_config.onAuthChange) _config.onAuthChange(_isLoggedIn);
    } catch(e) {
      console.error('checkAuth error', e);
    }
  }

  async function doLogin(password) {
    try {
      const res = await api(_config.loginPath, {method: 'POST', body: {password: password}});
      const data = await res.json();
      if (data.ok) {
        _isLoggedIn = true;
        if (data.csrf_token) _csrfToken = data.csrf_token;
        if (_config.onAuthChange) _config.onAuthChange(true);
        if (_config.onLoginSuccess) _config.onLoginSuccess();
        return {ok: true};
      } else {
        return {ok: false, error: data.error || '密码错误'};
      }
    } catch(e) {
      return {ok: false, error: '网络错误'};
    }
  }

  async function doLogout() {
    await api(_config.logoutPath, {method: 'POST'});
    _isLoggedIn = false;
    _csrfToken = '';
    if (_config.onAuthChange) _config.onAuthChange(false);
  }

  async function changePassword(newPw, confirmPw) {
    const res = await api(_config.changePasswordPath, {
      method: 'POST',
      body: {new_password: newPw, new_password_confirm: confirmPw}
    });
    const data = await res.json();
    if (data.ok && data.csrf_token) _csrfToken = data.csrf_token;
    return data;
  }

  var _escDiv = null;
  function escHtml(str) {
    if (!_escDiv) _escDiv = document.createElement('div');
    _escDiv.textContent = str;
    return _escDiv.innerHTML;
  }

  function getTheme() {
    return localStorage.getItem('mtc-theme') || null;
  }

  function applyTheme(manual) {
    const root = document.documentElement;
    document.body.style.transition = 'background 0.6s ease, color 0.4s ease';
    if (manual) {
      root.setAttribute('data-theme', manual);
    } else {
      root.removeAttribute('data-theme');
    }
  }

  function toggleTheme() {
    const manual = getTheme();
    let next;
    if (!manual) {
      next = 'dark';
    } else if (manual === 'dark') {
      next = 'light';
    } else {
      next = null;
    }
    if (next) {
      localStorage.setItem('mtc-theme', next);
    } else {
      localStorage.removeItem('mtc-theme');
    }
    applyTheme(next);
    return next;
  }

  function initTheme() {
    const stored = getTheme();
    if (stored) applyTheme(stored);
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function() {
      if (!getTheme()) applyTheme(null);
    });
  }

  initTheme();

  return {
    configure: configure,
    isLoggedIn: isLoggedIn,
    getCsrfToken: getCsrfToken,
    showToast: showToast,
    api: api,
    apiCached: apiCached,
    invalidateCache: invalidateCache,
    setCacheEntry: setCacheEntry,
    checkAuth: checkAuth,
    doLogin: doLogin,
    doLogout: doLogout,
    changePassword: changePassword,
    escHtml: escHtml,
    getTheme: getTheme,
    applyTheme: applyTheme,
    toggleTheme: toggleTheme,
  };
})();
