/*
 * Interface strings.
 *
 * Markup can be translated through data attributes:
 *   data-i18n             -> textContent
 *   data-i18n-placeholder -> placeholder
 *   data-i18n-title       -> title
 * Scripts use t("key", "fallback") for strings they build themselves.
 */
(function () {
  // English-only: the fallbacks in t() and the existing markup are the source.
  const dictionary = {};

  window.t = function (key, fallback, values) {
    let text = dictionary[key] || fallback || key;
    if (values) {
      Object.keys(values).forEach(function (name) {
        text = text.replace("{" + name + "}", values[name]);
      });
    }
    return text;
  };

  function apply(root) {
    (root || document).querySelectorAll("[data-i18n]").forEach(function (el) {
      const value = dictionary[el.dataset.i18n];
      if (value) el.textContent = value;
    });
    (root || document)
      .querySelectorAll("[data-i18n-placeholder]")
      .forEach(function (el) {
        const value = dictionary[el.dataset.i18nPlaceholder];
        if (value) el.placeholder = value;
      });
    (root || document).querySelectorAll("[data-i18n-title]").forEach(function (el) {
      const value = dictionary[el.dataset.i18nTitle];
      if (value) el.title = value;
    });
  }

  window.applyTranslations = apply;
  document.addEventListener("DOMContentLoaded", function () {
    apply(document);
  });
})();
