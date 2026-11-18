// 판정서 링크에서 QA 매니저가 결정을 남기는 칸. 저장소(db)의 decisions 컬렉션에 한 건씩 쌓고,
// 판정관(Claude)이 읽어 judge.py decide --source <문서 id>로 판정서에 옮긴다.
(function () {
  var D = JSON.parse(document.getElementById("df-data").textContent);
  var $ = function (id) { return document.getElementById(id); };
  var form = $("df"), status = $("df-status"), msg = $("df-msg"), list = $("df-list"), items = $("df-items");
  var recorded = {};
  D.recorded.forEach(function (id) { recorded[id] = true; });

  function say(text) { status.textContent = text; }

  function chosen() {
    var r = form.querySelector('input[name="decision"]:checked');
    return r ? r.value : "";
  }

  function fmt(at) { return String(at || "").slice(0, 16).replace("T", " "); }

  function render(docs, names) {
    items.textContent = "";
    docs.forEach(function (d) {
      var v = d.data() || {};
      var li = document.createElement("li");
      var who = v.by || "";
      var acct = v.by_id && names[v.by_id] && names[v.by_id].name ? " · 계정 " + names[v.by_id].name : "";
      var conds = (v.conditions_accepted || []).length ? " · 조건 " + v.conditions_accepted.join(", ") : "";
      li.textContent = fmt(v.at) + " · " + (D.verdicts[v.decision] || v.decision) + " · " + who + acct + conds
        + (v.note ? " — " + v.note : "") + (recorded[d.id] ? " (판정서에 기록됨)" : " (판정서 기록 전)");
      items.appendChild(li);
    });
    list.hidden = docs.length === 0;
  }

  async function start() {
    if (!window.claude || !window.claude.use) return;
    var db = await window.claude.use("db");
    if (!db) {
      say("이 화면에서는 결정을 저장할 수 없어요. 로그인했는지 확인하거나, Claude에게 결정을 말로 전달해 주세요.");
      return;
    }
    var user = await window.claude.use("user");
    var canWrite = user ? await user.can("data.write") : null;
    var col = db.collection("decisions");

    if (canWrite === false) {
      say("이 판정서에는 보기 권한만 있어요. 결정을 남기려면 소유자에게 참여자 이상 권한을 받아 주세요.");
    } else {
      say("결정을 고르고 이름·역할과 이유를 적어 저장하세요. 저장한 결정은 판정관이 판정서에 옮겨요.");
      form.hidden = false;
    }

    var loading = setTimeout(function () { list.hidden = true; }, 10000);
    col.orderBy("at", "desc").limit(20).onSnapshot(async function (snap) {
      clearTimeout(loading);
      var ids = [];
      snap.docs.forEach(function (d) { var v = d.data() || {}; if (v.by_id && ids.indexOf(v.by_id) < 0) ids.push(v.by_id); });
      var names = {};
      if (user && ids.length) { try { names = await user.profiles(ids); } catch (e) { names = {}; } }
      render(snap.docs, names);
    }, function () { clearTimeout(loading); });

    form.addEventListener("submit", async function (ev) {
      ev.preventDefault();
      var decision = chosen();
      var by = $("df-by").value.trim();
      var note = $("df-note").value.trim();
      var conds = Array.prototype.map.call(form.querySelectorAll('input[name="cond"]:checked'), function (c) { return c.value; });
      if (!decision) { msg.textContent = "결정을 골라 주세요."; return; }
      if (!by) { msg.textContent = "결정한 사람(이름·역할)을 적어 주세요."; $("df-by").focus(); return; }
      if (decision !== D.recommendation && !note) {
        msg.textContent = "추천(" + D.verdicts[D.recommendation] + ")과 다르게 결정하면 이유를 적어 주세요.";
        $("df-note").focus();
        return;
      }
      if (decision === "conditional" && D.conditions.length && !conds.length) {
        msg.textContent = "조건부 편입이면 받아들이는 조건을 하나 이상 골라 주세요.";
        return;
      }
      var btn = $("df-submit");
      btn.disabled = true;
      msg.textContent = "저장하는 중…";
      try {
        await col.add({
          decision: decision, by: by, note: note, conditions_accepted: conds,
          at: new Date().toISOString(), by_id: user ? await user.id() : null, judgment_at: D.generated_at
        });
        msg.textContent = "저장했어요. 판정관에게 '판정서에 옮겨 줘'라고 하면 기록돼요.";
        $("df-note").value = "";
      } catch (e) {
        if (e && e.code === "invalid_argument") {
          form.hidden = true;
          say("이 판정서에 결정을 저장할 권한이 없어요. 소유자에게 참여자 이상 권한을 받아 주세요.");
        } else if (e && e.code === "quota_exceeded") {
          msg.textContent = "저장 공간이 가득 차서 저장하지 못했어요. 판정서 소유자에게 알려 주세요.";
        } else {
          msg.textContent = "저장하지 못했어요. 잠시 뒤 다시 눌러 주세요.";
        }
      } finally {
        btn.disabled = false;
      }
    });

    form.addEventListener("change", function () {
      $("df-note-hint").textContent = chosen() !== D.recommendation ? "(추천과 다르게 결정해서 필수)" : "(선택)";
    });
    $("df-note-hint").textContent = chosen() !== D.recommendation ? "(추천과 다르게 결정해서 필수)" : "(선택)";
  }

  start().catch(function () { say("결정 입력 칸을 불러오지 못했어요. Claude에게 결정을 말로 전달해 주세요."); });
})();
