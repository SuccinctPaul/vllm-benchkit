/* 翻译挂件：在 Material 顶部右侧注入「🌐 翻译」下拉，按需懒加载 Google 页面翻译。
 * 原则：只翻译正文、不改文档原文；脚本仅在被点击时才加载，不影响站点其它资源，
 * 断网 / 无法访问 Google 时静默不生效（轻量、可降级）。 */
(function () {
  var LANGS = [
    ["zh-CN", "中文(简体)"],
    ["zh-TW", "中文(繁體)"],
    ["en", "English"],
    ["ja", "日本語"],
    ["ko", "한국어"],
  ];

  function injectWidget() {
    // 放进 Material 顶栏右侧 actions 区域
    var actions = document.querySelector(".md-header__inner > .md-header__option");
    var target = actions || document.querySelector(".md-header__inner");
    if (!target) return;

    var wrap = document.createElement("div");
    wrap.className = "md-translate-wrap";

    var btn = document.createElement("a");
    btn.className = "md-translate-btn";
    btn.href = "javascript:void(0)";
    btn.title = "整页自动翻译";
    btn.textContent = "🌐";

    var menu = document.createElement("div");
    menu.className = "md-translate-menu";
    LANGS.forEach(function (item) {
      var b = document.createElement("button");
      b.type = "button";
      b.textContent = item[1];
      b.addEventListener("click", function () {
        menu.dataset.open = "false";
        loadGoogleTranslate(item[0]);
      });
      menu.appendChild(b);
    });

    btn.addEventListener("click", function () {
      var open = menu.dataset.open === "true";
      menu.dataset.open = open ? "false" : "true";
    });
    document.addEventListener("click", function (e) {
      if (!wrap.contains(e.target)) menu.dataset.open = "false";
    });

    wrap.appendChild(btn);
    wrap.appendChild(menu);
    target.appendChild(wrap);
  }

  function loadGoogleTranslate(lang) {
    var host = document.querySelector("#google_translate_element");
    if (!host) {
      host = document.createElement("div");
      host.id = "google_translate_element";
      document.body.appendChild(host);
    }
    window.googleTranslateElementInit = function () {
      if (window.google && window.google.translate) {
        new google.translate.TranslateElement(
          { pageLanguage: "zh-CN", includedLanguages: "zh-CN,zh-TW,en,ja,ko",
            layout: google.translate.TranslateElement.InlineLayout.SIMPLE },
          "google_translate_element"
        );
        // 稍后再点一次以触发翻译
        setTimeout(function () {
          var sel = document.querySelector("#google_translate_element select");
          if (sel) sel.value = lang;
        }, 120);
      }
    };
    var s = document.createElement("script");
    s.src = "https://translate.google.com/translate_a/element.js?cb=googleTranslateElementInit";
    s.onerror = function () { /* 断网/受限时静默 */ };
    document.body.appendChild(s);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", injectWidget);
  } else {
    injectWidget();
  }
})();