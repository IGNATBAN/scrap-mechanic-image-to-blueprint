'use strict';

/* Строки интерфейса на двух языках.
 *
 * Словарь один на весь проект — data/i18n.json. Его читают и локальная
 * версия, и сайт, и генератор памятки по сборке на Python. Поэтому русский
 * и английский не могут разъехаться между версиями: строка правится в
 * одном месте.
 *
 * В разметке ставятся атрибуты:
 *   data-i18n       — заменить текст
 *   data-i18n-html  — заменить разметку (там, где внутри есть <b>)
 *   data-i18n-title — подсказка при наведении
 *   data-i18n-ph    — placeholder поля ввода
 * Русский текст остаётся в HTML как есть: страница читается ещё до того,
 * как отработает скрипт.
 */

const I18N = (() => {
  const st = { lang: 'ru', table: {}, listeners: [] };

  function detect() {
    const saved = localStorage.getItem('sm_pixel_lang');
    if (saved && st.table[saved]) return saved;
    const nav = (navigator.language || 'ru').slice(0, 2).toLowerCase();
    return st.table[nav] ? nav : (nav === 'ru' ? 'ru' : 'en');
  }

  /** Подстановка {name} — одинаково с питоновским format. */
  function fill(text, vars) {
    if (!vars) return text;
    return text.replace(/\{(\w+)\}/g, (m, key) => (key in vars ? vars[key] : m));
  }

  function t(key, vars) {
    const cur = st.table[st.lang] || {};
    const fallback = st.table.ru || {};
    const text = key in cur ? cur[key] : (key in fallback ? fallback[key] : key);
    return fill(text, vars);
  }

  function apply(root) {
    const scope = root || document;
    scope.querySelectorAll('[data-i18n]').forEach((el) => {
      el.textContent = t(el.dataset.i18n);
    });
    scope.querySelectorAll('[data-i18n-html]').forEach((el) => {
      el.innerHTML = t(el.dataset.i18nHtml);
    });
    scope.querySelectorAll('[data-i18n-title]').forEach((el) => {
      el.title = t(el.dataset.i18nTitle);
    });
    scope.querySelectorAll('[data-i18n-ph]').forEach((el) => {
      el.placeholder = t(el.dataset.i18nPh);
    });
    document.documentElement.lang = st.lang;
  }

  return {
    async load(url) {
      const raw = await (await fetch(url || 'data/i18n.json')).json();
      st.table = {};
      for (const [k, v] of Object.entries(raw)) if (!k.startsWith('_')) st.table[k] = v;
      st.lang = detect();
      return st.lang;
    },

    /** Для Node и тестов: подсунуть уже прочитанный словарь. */
    setTable(raw) {
      st.table = {};
      for (const [k, v] of Object.entries(raw)) if (!k.startsWith('_')) st.table[k] = v;
    },

    lang: () => st.lang,
    languages: () => Object.keys(st.table),

    set(lang) {
      if (!st.table[lang] || lang === st.lang) return st.lang;
      st.lang = lang;
      localStorage.setItem('sm_pixel_lang', lang);
      apply();
      st.listeners.forEach((fn) => fn(lang));
      return lang;
    },

    onChange(fn) { st.listeners.push(fn); },
    t,
    apply,

    /** Кнопки переключения языка в шапке. */
    mount(el) {
      el.innerHTML = this.languages()
        .map((code) => `<button type="button" data-lang="${code}"`
          + `${code === st.lang ? ' class="on"' : ''}>${code.toUpperCase()}</button>`)
        .join('');
      el.addEventListener('click', (e) => {
        const btn = e.target.closest('button[data-lang]');
        if (!btn) return;
        this.set(btn.dataset.lang);
        [...el.children].forEach((b) => b.classList.toggle('on', b.dataset.lang === st.lang));
      });
    },
  };
})();
