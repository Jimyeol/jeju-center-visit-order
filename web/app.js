/* 센터 방문 주문서 - 화면 동작 (3차).
   한 화면에 한 가지만 보인다. 자세한 것은 주소(#/…)가 있는 상세 페이지로 옮기고, 같은 창에서 넘어간다(새 탭 없음).
   브라우저 뒤로 가기와 "← 돌아가기"를 지원한다. 서버와 주고받는 방식은 그대로다.
   서버 응답의 글자는 모두 textContent 로만 넣는다(innerHTML 을 쓰지 않는다).
   수치는 서버(코드)가 계산한 값을 표기만 바꿔 보인다. 그림(일러스트)에는 글자·숫자를 넣지 않는다.
   명세서 번호·컬럼 영문·업종코드 같은 원래 글은 "근거 원문" 상자와 인쇄본에만 둔다. */
(function () {
  'use strict';

  var state = {
    status: null, options: null, run: null, rendered: null, purpose: '', inputKind: 'text',
    busy: false, csvBusy: false, csv: [], csvErrors: [], path: '', navSeq: 0, origin: '/home',
    cleared: null, lastTrigger: {}
  };
  var SVGNS = 'http://www.w3.org/2000/svg';

  /* ---------- 작은 도구 ---------- */

  function $(id) { return document.getElementById(id); }

  function append(node, kids) {
    for (var i = 0; i < kids.length; i++) {
      var k = kids[i];
      if (k === null || k === undefined || k === false) continue;
      if (Array.isArray(k)) { append(node, k); continue; }
      node.appendChild(k.nodeType ? k : document.createTextNode(String(k)));
    }
    return node;
  }

  function setAttrs(node, props) {
    if (!props) return;
    Object.keys(props).forEach(function (k) {
      var v = props[k];
      if (v === null || v === undefined || v === false) return;
      if (k === 'text') node.textContent = String(v);
      else if (k.slice(0, 2) === 'on' && typeof v === 'function') node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? '' : String(v));
    });
  }

  function el(tag, props) {
    var node = document.createElement(tag);
    setAttrs(node, props);
    return append(node, Array.prototype.slice.call(arguments, 2));
  }

  function sv(tag, props) {
    var node = document.createElementNS(SVGNS, tag);
    setAttrs(node, props);
    return append(node, Array.prototype.slice.call(arguments, 2));
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }

  function isNum(v) { return typeof v === 'number' && isFinite(v); }
  function arr(v) { return Array.isArray(v) ? v : (v === null || v === undefined || v === '' ? [] : [v]); }

  /* 값이 문자열·숫자·배열·객체 어느 것으로 와도 사람이 읽을 글로 바꾼다 */
  function txt(v) {
    if (v === null || v === undefined) return '';
    if (typeof v === 'string') return v;
    if (typeof v === 'number') return fmtNum(v);
    if (typeof v === 'boolean') return v ? '예' : '아니오';
    if (Array.isArray(v)) return v.map(txt).filter(Boolean).join(', ');
    if (typeof v === 'object') {
      var keys = ['label', 'name', 'ko', 'text', 'title', 'value', 'detail'];
      for (var i = 0; i < keys.length; i++) if (v[keys[i]] !== undefined && v[keys[i]] !== null && typeof v[keys[i]] !== 'object') return String(v[keys[i]]);
      if (v.dataset_no) return [v.dataset_no, v.dataset_name].filter(Boolean).join(' ');
      return Object.keys(v).map(function (k) { return txt(v[k]); }).filter(Boolean).join(' ');
    }
    return String(v);
  }

  /* 천 단위 쉼표. 금액은 원 단위 그대로 둔다(억·만으로 줄이지 않는다) */
  function fmtNum(n, digits) {
    if (!isNum(n)) return txt(n);
    var d = digits === undefined ? (Number.isInteger(n) ? 0 : 1) : digits;
    return n.toLocaleString('ko-KR', { minimumFractionDigits: d, maximumFractionDigits: d });
  }
  function fmtCount(v, unit) {
    if (isNum(v)) return fmtNum(v, Number.isInteger(v) ? 0 : 1) + (unit || '');
    return txt(v);
  }
  function fmtPct(v) { return isNum(v) ? fmtNum(v, 1) + '%' : txt(v); }

  function parseYm(v) {
    var m = /^\s*(\d{4})\s*[-./년]?\s*(\d{1,2})\s*월?\s*$/.exec(String(v));
    if (!m) return null;
    var mo = Number(m[2]);
    if (mo < 1 || mo > 12) return null;
    return { y: Number(m[1]), m: mo };
  }
  function fmtYm(v) {
    var p = parseYm(v);
    return p ? p.y + '-' + (p.m < 10 ? '0' : '') + p.m : txt(v);
  }
  /* "2024-08" -> "2024년 8월" (표기만 바꾼다) */
  function fmtYmKo(v) {
    var p = parseYm(v);
    return p ? p.y + '년 ' + p.m + '월' : txt(v);
  }
  /* 글 속의 "2024-08" 도 "2024년 8월" 로 */
  function ymInText(s) { return String(s || '').replace(/(\d{4})-(\d{2})(?![\d-])/g, function (m0) { return fmtYmKo(m0); }); }

  /* 배열에서 null 원소를 뺀다(서버가 빈 원소를 보내도 화면이 멈추지 않게) */
  function list(v) { return arr(v).filter(function (x) { return x !== null && x !== undefined; }); }

  /* ---------- 화면용 글 다듬기 (원문은 "근거 원문" 상자에 그대로 둔다) ---------- */

  function lastHangul(s) {
    var t = String(s || '').replace(/[\s)\]'"]+$/, '');
    var c = t.charCodeAt(t.length - 1);
    return (c >= 0xAC00 && c <= 0xD7A3) ? c : 0;
  }
  function hasBatchim(s) {
    var c = lastHangul(s);
    if (c) return (c - 0xAC00) % 28 !== 0;
    /* 숫자로 끝나면 읽는 소리로(1·3·6·7·8·0 은 받침이 있다) */
    var t = String(s || '').replace(/[\s)\]'"%]+$/, '');
    var d = t.charAt(t.length - 1);
    return /[136780]/.test(d);
  }
  /* josa('시간대', '은는') -> '시간대는' */
  function josa(word, pair) { return word + (hasBatchim(word) ? pair.charAt(0) : pair.charAt(1)); }
  /* "~로/으로" (받침이 있고 ㄹ 받침이 아니면 "으로") */
  function ro(word) {
    var c = lastHangul(word);
    if (!c) return word + '로';
    var jong = (c - 0xAC00) % 28;
    return word + (jong && jong !== 8 ? '으로' : '로');
  }

  /* 데이터 이름 끝의 판 표기(_v01, _v02)는 화면에서 "옛 판·새 판"으로(인쇄본·근거 원문은 원래 이름).
     뒤 조사는 "판"(받침 있음)에 맞춰 다시 고른다 */
  var PAN_J = { '은': '은', '는': '은', '이': '이', '가': '이', '을': '을', '를': '을', '과': '과', '와': '과', '으로': '으로', '로': '으로' };
  function dsName(s) {
    return txt(s).replace(/_v0*(\d+)(으로|로|은|는|이|가|을|를|과|와)?(?![\d가-힣])/g, function (m, n, j) {
      return (n === '1' ? ' 옛 판' : n === '2' ? ' 새 판' : ' ' + n + '판') + (j ? PAN_J[j] : '');
    });
  }

  /* 명세서 번호가 든 괄호 "(4-09, 4-14)", "(명세서 1-14 참고표)", "(4-15 6번 컬럼 …(…))" 를 통째로 뺀다 */
  function dropCodeParens(s) {
    var out = '', i = 0;
    while (i < s.length) {
      if (s.charAt(i) === '(' && /^\((?:명세서\s*)?\d-\d{2}(?!\d)/.test(s.slice(i, i + 16))) {
        var depth = 0, j = i;
        for (; j < s.length; j++) {
          if (s.charAt(j) === '(') depth += 1;
          else if (s.charAt(j) === ')') { depth -= 1; if (!depth) break; }
        }
        if (j < s.length) { out = out.replace(/\s+$/, ''); i = j + 1; continue; }
      }
      out += s.charAt(i);
      i += 1;
    }
    return out;
  }

  var NAME_MAP = {};
  function rememberNames(items) {
    list(items).forEach(function (d) { if (d.dataset_no && d.dataset_name) NAME_MAP[d.dataset_no] = dsName(d.dataset_name); });
  }
  var HUB_KO = { '1048': '공개 카드 데이터', '520': '공개 유동인구 데이터' };
  var JOSA_PAIR = { '은': '은는', '는': '은는', '이': '이가', '가': '이가', '을': '을를', '를': '을를', '과': '과와', '와': '과와' };

  /* 서버 글을 도민 말로: 허브 번호·명세서 번호·코드 괄호·옛 탭 이름·"LLM" 을 바꾼다. 값과 문장 뜻은 그대로 */
  function plain(v) {
    var s = dsName(v);
    if (!s) return '';
    s = dropCodeParens(s);
    /* "공개 허브 1048" 은 허브 이름 안에 이미 "공개" 가 있어 앞의 "공개" 를 뗀다 */
    s = s.replace(/공개\s+허브(?=\s*(1048|520))/g, '허브');
    s = s.replace(/허브\s+공개 데이터\(([^()]*)\)/g, function (m, inner) { return '공개 데이터(' + inner.replace(/(1048|520)\s*/g, '') + ')'; });
    s = s.replace(/'무엇을'에서/g, "'업종'에서");
    s = s.replace(/여러 달\(([^()]*)\)/g, function (m, inner) {
      return '여러 달(' + inner.replace(/(20\d{2})-?(0[1-9]|1[0-2])(?!\d)/g, function (m0, y, mo) { return y + '년 ' + Number(mo) + '월'; }) + ')';
    });
    s = s.replace(/허브\s*(1048|520)(\s*(은|는|이|가|을|를|과|와|의)(?=[\s.,]))?/g, function (m, no, _p, j) {
      var name = HUB_KO[no];
      if (j === '의') return name + '의';
      return j ? josa(name, JOSA_PAIR[j]) : name;
    });
    /* 목록에 없는 번호는 "명세서의 다른 자료", 두 번째로 나오는 다른 번호부터는 "또 다른 자료"(같은 번호는 같은 이름) */
    var unknownSeen = [];
    s = s.replace(/(^|[^\d\-~.])(\d-\d{2})(?!\d)(\s*(은|는|이|가|을|를|의)(?=\s))?/g, function (m, pre, no, _p, j) {
      var name = NAME_MAP[no];
      if (!name) {
        if (unknownSeen.indexOf(no) < 0) unknownSeen.push(no);
        var nm = unknownSeen.indexOf(no) === 0 ? '명세서의 다른 자료' : '또 다른 자료';
        return pre + nm + (j ? (j === '의' ? '의' : josa('자료', JOSA_PAIR[j]).slice(2)) : '');
      }
      if (j === '의') return pre + name + '의';
      return pre + (j ? josa(name, JOSA_PAIR[j]) : name);
    });
    /* "포스"(가게 계산대 기록), "~종"(데이터 가짓수)은 처음 보는 사람에게 낯설다 */
    s = s.replace(/포스/g, '가게 계산대(POS)')
      .replace(/여러 종을/g, '여러 데이터를').replace(/한 종으로/g, '한 가지 데이터로').replace(/데이터 (\d+)종/g, '데이터 $1가지')
      .replace(/계열 (\d+)종/g, '데이터 $1종').replace(/(\d+)종(?=이다|입니다|이에요)/g, '$1가지')
      .replace(/(\d+)종(으로|은|는|이|가|을|를|과|와|로|의|도|만)?(?![가-힣])/g, function (m, n, j) {
        var J = { '으로': '로', '로': '로', '은': '는', '는': '는', '이': '가', '가': '가', '을': '를', '를': '를', '과': '와', '와': '와', '의': '의', '도': '도', '만': '만' };
        return n + '가지' + (j ? J[j] : '');
      })
      .replace(/k값 3 미만/g, '개인을 알아볼 수 있는 작은 값(k값 3 미만)').replace(/격자(?!\()/g, '격자(네모 구역)');
    s = s.replace(/\s*\(모델\s+[^()]*\)/g, '').replace(/claude-[\w.-]+/g, 'AI 모델')
      .replace(/LLM 모드가/g, 'AI를 쓰게').replace(/LLM 모드로/g, 'AI를 써서').replace(/LLM 모드/g, 'AI를 쓸 때')
      .replace(/규칙 모드\(축소 모드\)/g, 'AI 없이 실행할 때').replace(/규칙 모드로/g, 'AI 없이').replace(/규칙 모드/g, 'AI 없이 실행');
    /* "컬럼"·"버렸다" 는 낯선 말이라 "칸"·"뺐다" 로 */
    s = s.replace(/컬럼명/g, '칸 이름').replace(/컬럼/g, '칸').replace(/헤더\s*줄/g, '칸 이름 줄').replace(/헤더가/g, '첫 줄이').replace(/헤더는/g, '첫 줄은').replace(/헤더를/g, '첫 줄을').replace(/헤더/g, '첫 줄')
      .replace(/버렸고/g, '뺐고').replace(/버렸다/g, '뺐다').replace(/버렸어요/g, '뺐어요').replace(/버렸습니다/g, '뺐어요');
    s = s.replace(/칸으로 고르기/g, '직접 고르기')
      .replace(/LLM\s*(이|가)(?=\s)/g, 'AI가').replace(/LLM\s*(을|를)(?=\s)/g, 'AI를')
      .replace(/LLM\s*(은|는)(?=\s)/g, 'AI는').replace(/LLM\s*(으로|로)(?=\s)/g, 'AI로').replace(/LLM\s*의(?=\s)/g, 'AI의').replace(/LLM/g, 'AI');
    /* "값 'X'은(는)" 같은 두 갈래 조사는 앞말에 맞춰 하나로 */
    s = s.replace(/([^\s(]+?)(은\(는\)|이\(가\)|을\(를\)|과\(와\))/g, function (m, w, p) { return josa(w, p.charAt(0) + p.charAt(2)); });
    return s.replace(/\s{2,}/g, ' ').replace(/\s+([.,])(?=\s|$)/g, '$1').trim();
  }

  /* 서버의 "-다" 끝을 해요체로(뜻은 그대로). 원문은 근거 원문 상자에 둔다 */
  var HAEYO = [[/보입니다/g, '보여요'], [/보인다/g, '보여요'], [/아닙니다/g, '아니에요'], [/아니다/g, '아니에요'],
    [/짓습니다/g, '지어요'], [/짓는다/g, '지어요'], [/짓다/g, '지어요'], [/까지다/g, '까지예요'], [/볼 수 없다/g, '볼 수 없어요'], [/수 없다/g, '수 없어요'], [/수 있다/g, '수 있어요'], [/없다/g, '없어요'], [/있다/g, '있어요'],
    [/나눠 본다/g, '나눠 봐요'], [/본다/g, '봐요'], [/받는다/g, '받아요'], [/적는다/g, '적어요'], [/한다/g, '해요'], [/된다/g, '돼요'], [/않다/g, '않아요'],
    [/같다/g, '같아요'], [/다르다/g, '달라요'], [/했다/g, '했어요'], [/였다/g, '였어요'], [/([가-힣])었다/g, '$1었어요'], [/([가-힣])았다/g, '$1았어요'],
    [/뺐다/g, '뺐어요'], [/골랐다/g, '골랐어요'], [/썼다/g, '썼어요'], [/이다/g, '이에요'], [/데이터다/g, '데이터예요'], [/값이다/g, '값이에요'],
    [/합쳐진다/g, '합쳐져요'], [/숨긴다/g, '숨겨요'], [/가린다/g, '가려요'], [/나온다/g, '나와요'],
    [/합니다/g, '해요'], [/됩니다/g, '돼요'], [/입니다/g, '이에요'], [/십시오/g, '세요']];
  function haeyo(v) {
    var s = String(v || '');
    HAEYO.forEach(function (p) {
      s = s.replace(new RegExp(p[0].source + '(?=[.()\\s,]|$)', 'g'), p[1]);
    });
    /* "-습니다" 는 앞 글자 모음에 맞춰 "아요/어요" (않습니다 -> 않아요, 없습니다 -> 없어요) */
    s = s.replace(/([가-힣])습니다(?=[.()\s,]|$)/g, function (m, c) {
      var jung = Math.floor(((c.charCodeAt(0) - 0xAC00) % 588) / 28);
      var past = (c.charCodeAt(0) - 0xAC00) % 28 === 20;
      return c + (!past && (jung === 0 || jung === 8) ? '아요' : '어요');
    });
    s = s.replace(/하였어요/g, '했어요').replace(/되었어요/g, '됐어요')
      .replace(/뺀다(?=[.()\s,]|$)/g, '빼요').replace(/쓴다(?=[.()\s,]|$)/g, '써요').replace(/고른다(?=[.()\s,]|$)/g, '골라요').replace(/센다(?=[.()\s,]|$)/g, '세요');
    /* "채웠다", "넣었다" 처럼 ㅆ 받침 뒤 "다" 는 "어요" 로 */
    s = s.replace(/([가-힣])다(?=[.()\s,]|$)/g, function (m, c) { return (c.charCodeAt(0) - 0xAC00) % 28 === 20 ? c + '어요' : m; });
    /* 받침 없는 말 뒤의 "이에요"는 "예요"로 */
    return s.replace(/([가-힣])이에요/g, function (m, c) { return hasBatchim(c) ? m : c + '예요'; });
  }
  function easy(v) { return haeyo(plain(v)); }
  /* "같은 데이터로 가능한 것: …", "다른 측정값: …" 같은 머리말을 뗀다 */
  function dropLead(s) { return String(s || '').replace(/^(같은 데이터로 가능한 것|다른 측정값|이용자 범위가 다른 데이터)\s*:\s*/, ''); }

  /* 첫 문장만 (". " 또는 끝에서 자른다) */
  function firstSentence(s) {
    var m = /^(.*?\.)(\s|$)/.exec(s);
    return m ? m[1] : s;
  }

  /* 계절 지수를 "평소보다 10% 많음" 으로 (평균 100, 또는 평균 1 인 지수) */
  function indexWords(idx) {
    if (!isNum(idx)) return '';
    var d = idx < 10 ? (idx - 1) * 100 : idx - 100;
    var r = Math.round(Math.abs(d));
    if (r < 1) return '평소와 비슷';
    return '평소보다 ' + r + '% ' + (d > 0 ? '많음' : '적음');
  }

  function metricUnit(metric) {
    var s = String(metric || '');
    if (s.indexOf('금액') >= 0) return '원';
    if (s.indexOf('건수') >= 0) return '건';
    if (s.indexOf('이용자') >= 0) return '명';
    return '';
  }

  function nowText() {
    var d = new Date(), p = function (n) { return (n < 10 ? '0' : '') + n; };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  }

  /* ---------- 선 아이콘 (viewBox 0 0 24 24, 모두 aria-hidden) ---------- */

  var ICONS = {
    printer: ['M7 9V3h10v6 M7 17H4v-7h16v7h-3 M7 14h10v7H7z'],
    'check-circle': [{ t: 'circle', cx: 12, cy: 12, r: 9 }, 'M8 12.5l2.5 2.5L16 9.5'],
    check: ['M6 12.5l4 4L18 8'],
    alert: ['M12 3l9.5 17h-19z M12 10v4 M12 17.5v.5'],
    info: [{ t: 'circle', cx: 12, cy: 12, r: 9 }, 'M12 11v6 M12 7.5v.5'],
    'x-circle': [{ t: 'circle', cx: 12, cy: 12, r: 9 }, 'M9 9l6 6 M15 9l-6 6'],
    'none-circle': [{ t: 'circle', cx: 12, cy: 12, r: 9, 'stroke-dasharray': '3 3' }, 'M8 12h8'],
    upload: ['M7 18a4.5 4.5 0 0 1-.5-9A6 6 0 0 1 18 8a4 4 0 0 1 0 10 M12 12v8 M9 15l3-3 3 3'],
    lock: [{ t: 'rect', x: 5, y: 11, width: 14, height: 10, rx: 2 }, 'M8 11V8a4 4 0 0 1 8 0v3'],
    'arrow-left': ['M19 12H5 M11 6l-6 6 6 6'],
    'chevron-right': ['M9 6l6 6-6 6'],
    search: [{ t: 'circle', cx: 11, cy: 11, r: 6 }, 'M16 16l4 4'],
    pin: ['M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z', { t: 'circle', cx: 12, cy: 10, r: 2.5 }],
    store: ['M4 10v10h16V10 M3 10l2-6h14l2 6z M9 20v-5h6v5'],
    users: [{ t: 'circle', cx: 9, cy: 8, r: 3.5 }, 'M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6 M16 4.6a3.5 3.5 0 0 1 0 6.8 M21 20c0-2.6-1.6-4.8-4-5.6'],
    layers: ['M12 3l9 5-9 5-9-5z M3 13l9 5 9-5'],
    calendar: [{ t: 'rect', x: 3, y: 5, width: 18, height: 16, rx: 2 }, 'M3 10h18 M8 3v4 M16 3v4'],
    clock: [{ t: 'circle', cx: 12, cy: 12, r: 9 }, 'M12 7v5l3 2'],
    table: [{ t: 'rect', x: 3, y: 4, width: 18, height: 16, rx: 2 }, 'M3 9h18 M3 14.5h18 M9 9v11'],
    unplug: ['M7 7l10 10 M3 21l4-4 M17 7l4-4 M8 12l-2 2a3 3 0 0 0 4 4l2-2 M16 12l2-2a3 3 0 0 0-4-4l-2 2'],
    restart: ['M4 12a8 8 0 1 0 2.3-5.6 M4 4v4h4']
  };

  function icon(name, cls) {
    var s = sv('svg', { viewBox: '0 0 24 24', class: 'ico' + (cls ? ' ' + cls : ''), 'aria-hidden': 'true', focusable: 'false',
      fill: 'none', stroke: 'currentColor', 'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round' });
    arr(ICONS[name]).forEach(function (p) {
      if (typeof p === 'string') { s.appendChild(sv('path', { d: p })); return; }
      var a = {};
      Object.keys(p).forEach(function (k) { if (k !== 't') a[k] = p[k]; });
      s.appendChild(sv(p.t, a));
    });
    return s;
  }

  /* index.html 의 <span data-icon="이름"> 자리에 아이콘을 넣는다 */
  function fillIcons(root) {
    Array.prototype.forEach.call((root || document).querySelectorAll('[data-icon]'), function (slot) {
      if (slot.firstChild) return;
      slot.classList.add('ico-slot');
      slot.setAttribute('aria-hidden', 'true');
      slot.appendChild(icon(slot.getAttribute('data-icon')));
    });
  }

  function badge(kind, iconName, text) {
    return el('span', { class: 'badge badge-' + kind }, iconName ? icon(iconName) : null, text);
  }

  /* ---------- 일러스트 (선 그림, 글자·숫자 없음, 청록 포인트 한 곳, 모두 aria-hidden) ----------
     도형마다 [태그, 속성, 색 이름, 자식]. 색 이름: w 흰 면, wa 흰 면 + 청록 선, as 청록 선, af 청록 면,
     wk 흰 선, dash 점선, f2 옅은 회색 면. 없으면 먹색 선만. */

  var BUBBLE_BACK = ['path', { d: 'M58 40H72A14 14 0 0 1 78 66.6L84 74L70 68H58A14 14 0 0 1 58 40Z' }, 'w'];
  var BUBBLE_FRONT = ['path', { d: 'M30 18H46A20 20 0 0 1 46 58H34L20 68L24 55.5A20 20 0 0 1 30 18Z' }, 'w'];

  var ILLU = {
    ask: { vb: '0 0 96 96', s: [BUBBLE_BACK, BUBBLE_FRONT,
      ['path', { d: 'M32.5 32a5.5 5.5 0 1 1 7.5 5.1c-1.4.6-2 1.6-2 3v1.6 M38 47.6v.2', 'stroke-width': 3 }, 'as']] },
    'list-ok': { vb: '0 0 96 96', s: [BUBBLE_BACK, BUBBLE_FRONT,
      ['path', { d: 'M29.5 38.5l6.5 6.5 13-13', 'stroke-width': 3 }, 'as']] },
    'doc-search': { vb: '0 0 96 96', s: [
      ['path', { d: 'M22 12H56L70 26V82H22Z' }, 'w'],
      ['path', { d: 'M56 12V26H70' }],
      ['path', { d: 'M32 36H52 M32 46H56 M32 56H44' }],
      ['circle', { cx: 63, cy: 62, r: 14 }, 'wa'],
      ['path', { d: 'M73 72L84 83', 'stroke-width': 3.5 }, 'as']] },
    'bubble-chart': { vb: '0 0 96 96', s: [
      ['path', { d: 'M34 16H62A26 26 0 0 1 62 68H40L28 80L31 66A26 26 0 0 1 34 16Z' }, 'w'],
      ['rect', { x: 34, y: 46, width: 7, height: 12, rx: 1.5 }, 'af'],
      ['rect', { x: 45, y: 38, width: 7, height: 20, rx: 1.5 }, 'af'],
      ['rect', { x: 56, y: 30, width: 7, height: 28, rx: 1.5 }, 'af']] },
    folder: { vb: '0 0 96 96', s: [
      ['path', { d: 'M12 46L20 34H76L84 46' }],
      ['rect', { x: 26, y: 14, width: 30, height: 38, rx: 3 }, 'w'],
      ['rect', { x: 38, y: 20, width: 30, height: 38, rx: 3 }, 'w'],
      ['path', { d: 'M45 31H61 M45 39H61' }, 'as'],
      ['path', { d: 'M12 46H84L78 82H18Z' }, 'w']] },
    'pin-store': { vb: '0 0 96 96', s: [
      ['path', { d: 'M6 84H90' }],
      ['rect', { x: 42, y: 46, width: 42, height: 38 }, 'w'],
      ['path', { d: 'M38 46L44 32H82L88 46Z' }, 'w'],
      ['path', { d: 'M57 84V68H69V84' }],
      ['path', { d: 'M24 66s-15-13-15-26a15 15 0 0 1 30 0c0 13-15 26-15 26z' }, 'wa'],
      ['circle', { cx: 24, cy: 40, r: 5 }, 'as']] },
    'line-doc': { vb: '0 0 96 96', s: [
      ['rect', { x: 14, y: 12, width: 68, height: 72, rx: 6 }, 'w'],
      ['path', { d: 'M26 72H70 M26 72V26' }],
      ['path', { d: 'M30 62L42 48L52 54L67 34' }, 'as']] },
    'stamp-check': { vb: '0 0 96 96', s: [
      ['path', { d: 'M16 10H60L72 22V86H16Z' }, 'w'],
      ['path', { d: 'M60 10V22H72' }],
      ['path', { d: 'M26 32H54 M26 42H60 M26 52H44' }],
      ['circle', { cx: 66, cy: 68, r: 16 }, 'wa'],
      ['path', { d: 'M59 68l5 5 9-10', 'stroke-width': 3 }, 'as']] },
    'fork-two': { vb: '0 0 96 96', s: [
      ['path', { d: 'M48 84V58 M48 58C48 42 26 40 24 22 M48 58C48 42 70 40 72 22' }],
      ['circle', { cx: 24, cy: 17, r: 6 }, 'w'],
      ['circle', { cx: 72, cy: 17, r: 6 }, 'af'],
      ['circle', { cx: 48, cy: 86, r: 4 }, 'w']] },
    none: { vb: '0 0 96 96', s: [
      ['path', { d: 'M12 26H38L46 34H84V80H12Z' }, 'w'],
      ['circle', { cx: 40, cy: 57, r: 12 }, 'dash'],
      ['circle', { cx: 64, cy: 50, r: 12 }, 'wa'],
      ['path', { d: 'M73 59L84 70', 'stroke-width': 3.5 }, 'as']] },
    oos: { vb: '0 0 96 96', s: [
      ['path', { d: 'M48 12V86 M28 86H68' }],
      ['path', { d: 'M48 20H76L84 29L76 38H48Z' }, 'wa'],
      ['path', { d: 'M48 46H20L12 55L20 64H48Z' }, 'w']] },
    'drop-file': { vb: '0 0 96 96', s: [
      ['rect', { x: 34, y: 4, width: 28, height: 34, rx: 3 }, 'w'],
      ['path', { d: 'M41 14H55 M41 22H55' }],
      ['path', { d: 'M48 42V60 M41 53L48 60L55 53', 'stroke-width': 3 }, 'as'],
      ['path', { d: 'M12 68L20 58H76L84 68' }],
      ['path', { d: 'M12 68H84L78 90H18Z' }, 'w']] },
    'table-doc': { vb: '0 0 96 96', s: [
      ['rect', { x: 14, y: 14, width: 68, height: 68, rx: 6 }, 'w'],
      ['rect', { x: 38, y: 34, width: 22, height: 16 }, 'af'],
      ['path', { d: 'M14 34H82 M14 50H82 M14 66H82 M38 14V82 M60 14V82' }]] },
    route: { vb: '0 0 96 96', s: [
      ['path', { d: 'M20 70C32 70 32 30 48 30S64 70 76 70', 'stroke-dasharray': '5 6' }],
      ['circle', { cx: 18, cy: 70, r: 7 }, 'w'],
      ['circle', { cx: 48, cy: 30, r: 7 }, 'w'],
      ['circle', { cx: 78, cy: 70, r: 7 }, 'af']] },
    gauge: { vb: '0 0 96 96', s: [
      ['rect', { x: 20, y: 14, width: 56, height: 72, rx: 6 }, 'w'],
      ['rect', { x: 36, y: 8, width: 24, height: 12, rx: 3 }, 'w'],
      ['path', { d: 'M30 37l4 4 8-8', 'stroke-width': 3 }, 'as'],
      ['path', { d: 'M48 37H66 M30 54H40 M48 54H66 M30 70H40 M48 70H62' }]] },
    'pick-list': { vb: '0 0 96 96', s: [
      ['rect', { x: 16, y: 12, width: 64, height: 72, rx: 6 }, 'w'],
      ['rect', { x: 26, y: 24, width: 12, height: 12, rx: 2 }],
      ['rect', { x: 26, y: 42, width: 12, height: 12, rx: 2 }, 'af'],
      ['path', { d: 'M28.5 48l3 3 4.5-5.5' }, 'wk'],
      ['rect', { x: 26, y: 60, width: 12, height: 12, rx: 2 }],
      ['path', { d: 'M46 30H70 M46 48H70 M46 66H62' }]] },
    unplug: { vb: '0 0 48 36', s: [
      ['path', { d: 'M0 18H4' }],
      ['rect', { x: 4, y: 12, width: 14, height: 12, rx: 3 }, 'w'],
      ['path', { d: 'M18 15h4 M18 21h4' }],
      ['rect', { x: 30, y: 10, width: 14, height: 16, rx: 3 }, 'w'],
      ['path', { d: 'M35 15v2 M39 15v2' }],
      ['path', { d: 'M25 6l1 4 M25 30l1-4' }, 'as']] },
    'file-bad': { vb: '0 0 48 48', s: [
      ['path', { d: 'M10 4H30L38 12V44H10Z' }, 'w'],
      ['path', { d: 'M30 4V12H38' }],
      ['path', { d: 'M18 22l12 12 M30 22L18 34' }]] }
  };

  function illuShapes(parent, shapes) {
    shapes.forEach(function (sh) {
      var a = {};
      Object.keys(sh[1] || {}).forEach(function (k) { a[k] = sh[1][k]; });
      if (sh[2]) a['class'] = 'i-' + sh[2];
      var node = sv(sh[0], a);
      if (sh[3]) illuShapes(node, sh[3]);
      parent.appendChild(node);
    });
  }

  function illu(name, cls) {
    var def = ILLU[name];
    if (!def) return null;
    var s = sv('svg', { viewBox: def.vb, class: 'illu' + (cls ? ' ' + cls : ''), 'aria-hidden': 'true', focusable: 'false' });
    illuShapes(s, def.s);
    return s;
  }

  /* index.html 의 <span data-illu="이름"> 자리에 그림을 넣는다 */
  function fillIllus(root) {
    Array.prototype.forEach.call((root || document).querySelectorAll('[data-illu]'), function (slot) {
      if (slot.firstChild) return;
      slot.setAttribute('aria-hidden', 'true');
      var g = illu(slot.getAttribute('data-illu'));
      if (g) slot.appendChild(g);
    });
  }
  function setIllu(slot, name) {
    clear(slot);
    slot.setAttribute('aria-hidden', 'true');
    var g = name ? illu(name) : null;
    if (g) slot.appendChild(g);
  }
  function illuSlot(name, cls) {
    return el('span', { class: 'illu-slot ' + cls, 'aria-hidden': 'true' }, illu(name));
  }

  /* 알림은 마지막 것 하나만 읽힌다(겹쳐 부르면 앞 것을 지운다) */
  var sayTimer = null;
  function say(msg) {
    var live = $('liveMsg');
    live.textContent = '';
    if (sayTimer) window.clearTimeout(sayTimer);
    sayTimer = window.setTimeout(function () { sayTimer = null; live.textContent = msg; }, 60);
  }

  /* ---------- 서버 호출 ---------- */

  function ApiError(kind, message, action, status) {
    this.kind = kind; this.message = message; this.action = action || ''; this.status = status || 0;
  }

  function api(method, path, body, headers) {
    var opt = { method: method, headers: headers || {}, cache: 'no-store' };
    if (body !== undefined) opt.body = body;
    return fetch(path, opt).then(function (res) {
      return res.text().then(function (raw) {
        var data = null;
        try { data = raw ? JSON.parse(raw) : null; } catch (e) { data = null; }
        if (!res.ok || (data && data.error)) {
          var err = data && data.error;
          if (typeof err === 'string') err = { message: err };
          err = err || {};
          throw new ApiError('server',
            err.message || ('프로그램이 요청을 처리하지 못했어요(HTTP ' + res.status + ').'),
            err.action || (err.message ? '' : '같은 일이 되풀이되면 처음에 연 검은 창을 닫고 프로그램을 다시 실행해 주세요.'),
            res.status);
        }
        if (data === null || typeof data !== 'object') {
          throw new ApiError('server', '프로그램의 응답을 읽을 수 없어요.', '화면을 새로 고친 뒤(F5) 다시 해 보세요.', res.status);
        }
        setDown(false);
        return data;
      });
    }, function () {
      setDown(true);
      throw new ApiError('down', '프로그램과 연결이 끊겼어요.',
        '처음에 연 검은 창(run.bat, 맥·리눅스는 run.sh)이 닫혔으면 다시 실행한 뒤 [다시 연결]을 눌러 주세요.');
    });
  }

  function setDown(on) {
    var was = !$('downBanner').hidden;
    $('downBanner').hidden = !on;
    if (!on && was) clearDownErrors();
  }

  /* 오류: 원인 한 줄 + 할 일 한 줄 */
  function showError(box, err) {
    clear(box);
    var message = (err && err.message) || '알 수 없는 오류가 났어요.';
    var dl = el('dl', null, el('dt', { text: '원인' }), el('dd', { text: message }));
    if (err && err.action) append(dl, [el('dt', { text: '할 일' }), el('dd', { text: err.action })]);
    box.appendChild(el('div', { class: 'errorbox-in' }, icon('x-circle', 'err-ico'), dl));
    box.hidden = false;
    /* 연결 끊김 오류는 다시 연결되면 함께 내린다 */
    if (err && err.kind === 'down') box.setAttribute('data-down', '1'); else box.removeAttribute('data-down');
  }

  function clearDownErrors() {
    Array.prototype.forEach.call(document.querySelectorAll('.errorbox[data-down]'), function (b) { b.hidden = true; b.removeAttribute('data-down'); });
  }

  /* ---------- 프로그램 상태 ---------- */

  function specSentence(spec) {
    if (!spec || spec.datasets === undefined || spec.datasets === null) return '명세서를 읽지 못했어요';
    var s = '명세서 ' + txt(spec.version) + (spec.date ? '(' + txt(spec.date) + ')' : '') + ' 기준 데이터 ' + fmtCount(spec.datasets) + '가지';
    if (spec.ref_tables !== undefined && spec.ref_tables !== null) s += ', 참고표 ' + fmtCount(spec.ref_tables) + '가지';
    if (spec.columns !== undefined && spec.columns !== null) s += ', 항목 ' + fmtCount(spec.columns) + '개';
    return s;
  }

  function publicSentence(name, d) {
    if (!d || d.rows === undefined || d.rows === null) return name + ' 읽지 못함';
    var s = name + ' ' + fmtCount(d.rows) + '줄';
    if (d.first || d.last) s += '(' + fmtYmKo(d.first) + '~' + fmtYmKo(d.last) + ')';
    if (d.placeholder) s += ', 대체 파일';
    return s;
  }

  function llmOn() { return !!(state.status && state.status.llm && state.status.llm.enabled); }

  function statusFacts(s) {
    var checks = Array.isArray(s.selfcheck) ? s.selfcheck : [];
    var failed = checks.filter(function (c) { return c && !c.ok; });
    var onlyMode = failed.length > 0 && failed.every(function (c) { return String(c.name || '').indexOf('실행 모드') >= 0; });
    return {
      checks: checks, okCount: checks.length - failed.length, failed: failed, onlyMode: onlyMode,
      problem: !checks.length || (failed.length > 0 && !onlyMode)
    };
  }

  /* 상태가 오면: 대체 파일 띠, 홈의 한 줄 안내를 정한다 */
  function renderStatusStrip(s) {
    state.status = s;
    var pub = s.public || {};
    var ph = !!(s.placeholder || (pub.d1048 && pub.d1048.placeholder) || (pub.d520 && pub.d520.placeholder));
    $('phBanner').hidden = !ph;
    renderHomeNote();
    if (state.path === '/status') renderStatusPage();
  }

  function loadStatus() {
    return api('GET', '/api/status').then(renderStatusStrip, function (err) {
      state.statusError = err;
      renderHomeNote();
    });
  }

  /* 홈 카드 위 한 줄 안내. 한 번에 하나만(점검 문제 > AI 없이 > 지난 결과 > 넣은 파일 경고) */
  function renderHomeNote() {
    var box = clear($('homeNote'));
    var s = state.status;
    var line = null;
    if (state.statusError || (s && statusFacts(s).problem)) {
      line = [icon('alert'), el('span', { text: '프로그램 점검에서 확인할 것이 있어요.' }), el('a', { class: 'link', href: '#/status' }, '보기 ›')];
      box.className = 'note-line warn';
    } else if (s && s.llm && !s.llm.enabled) {
      var failed = s.llm.checked === 'failed' || statusFacts(s).onlyMode && s.llm.reason && /실패|연결/.test(txt(s.llm.reason));
      line = [icon('info'), el('span', { text: failed ? 'AI 연결을 확인하지 못해 AI 없이 만들어요.' : '지금은 AI 없이 실행 중이에요.' }),
        el('a', { class: 'link', href: '#/pick' }, '직접 고르기가 더 정확해요 ›')];
      box.className = 'note-line';
    } else if (s && s.leftover && !state.run && !state.csv.length && ((s.leftover.files || 0) + (s.leftover.results || 0)) > 0) {
      line = [icon('alert'), el('span', { text: '지난번 결과가 남아 있어요.' }), el('button', { type: 'button', class: 'link', onclick: askClear }, '지우기')];
      box.className = 'note-line warn';
    } else if (state.csv.length) {
      line = [icon('info'), el('span', { text: '새로 만들면 넣은 파일 결과는 화면에서 사라져요.' })];
      box.className = 'note-line';
    }
    if (line) append(box, line);
    box.hidden = !line;
  }

  /* ---------- 질문 · 직접 고르기 ---------- */

  function optValue(o) {
    if (o !== null && typeof o === 'object') {
      var v = o.key !== undefined ? o.key : (o.value !== undefined ? o.value : (o.id !== undefined ? o.id : o.label));
      return String(v === undefined || v === null ? '' : v);
    }
    return String(o);
  }
  function optLabel(o) {
    if (o !== null && typeof o === 'object') return String(o.label || o.name || o.title || optValue(o));
    return String(o);
  }

  function fillSelect(sel, items, emptyLabel) {
    clear(sel);
    sel.appendChild(el('option', { value: '', text: emptyLabel }));
    arr(items).forEach(function (o) { sel.appendChild(el('option', { value: optValue(o), text: optLabel(o) })); });
  }

  /* 항목에 group 이 있으면 묶음(optgroup)으로 나눠 넣는다 */
  function fillGrouped(sel, items, emptyLabel) {
    clear(sel);
    sel.appendChild(el('option', { value: '', text: emptyLabel }));
    var groups = {}, order = [];
    arr(items).forEach(function (o) {
      var g = (o && o.group) || '';
      if (!groups[g]) { groups[g] = []; order.push(g); }
      groups[g].push(o);
    });
    order.forEach(function (g) {
      var box = g ? el('optgroup', { label: g }) : sel;
      groups[g].forEach(function (o) { box.appendChild(el('option', { value: optValue(o), text: optLabel(o) })); });
      if (g) sel.appendChild(box);
    });
  }

  function renderOptions(o) {
    /* 다시 연결해 목록을 새로 받을 때 사용자가 이미 고른 값은 그대로 둔다 */
    var keep = state.options ? readSlots() : null;
    var pickedEx = -1;
    Array.prototype.forEach.call(document.querySelectorAll('#examples .chip'), function (b, i) { if (b.classList.contains('picked')) pickedEx = i; });
    state.options = o;
    renderOptionLists(o);
    if (keep) {
      setSelect($('slotWho'), keep.who);
      setSelect($('slotWhat'), keep.what);
      setSelect($('slotWhen'), keep.when);
      setSelect($('slotRegion'), keep.region);
      Array.prototype.forEach.call(document.querySelectorAll('#slotUnits input'), function (c) { c.checked = keep.units.indexOf(c.value) >= 0; });
      if (pickedEx >= 0) markExample(document.querySelectorAll('#examples .chip')[pickedEx] || null);
    }
  }

  function renderOptionLists(o) {
    fillSelect($('slotWho'), o.who, '고르지 않음');
    fillGrouped($('slotWhat'), o.what, '고르지 않음');
    fillSelect($('slotWhen'), o.when, '고르지 않음');

    /* 범위 안내(서버 글)는 도움말 페이지에 쉬운 말로 */
    $('scopeText').textContent = easy(txt(o.scope && o.scope.text)) || '카드 소비와 방문·유동인구에 관한 질문에 답해요.';

    var region = clear($('slotRegion'));
    region.appendChild(el('option', { value: '', text: '고르지 않음(제주도 전체)' }));
    var groups = [['district7', '권역'], ['emd', '읍면'], ['bjd', '동(법정동)']];
    var regions = o.regions || {};
    groups.forEach(function (g) {
      var items = arr(regions[g[0]]);
      if (!items.length) return;
      var og = el('optgroup', { label: g[1] });
      items.forEach(function (it) { og.appendChild(el('option', { value: optValue(it), text: optLabel(it), 'data-kind': g[0] })); });
      region.appendChild(og);
    });

    var units = clear($('slotUnits'));
    arr(o.units).forEach(function (u, i) {
      var id = 'unit_' + i;
      units.appendChild(el('label', { class: 'check-chip', for: id }, el('input', { type: 'checkbox', id: id, name: 'units', value: optValue(u) }), el('span', { text: optLabel(u) })));
    });
    $('unitsField').hidden = arr(o.units).length === 0;

    /* 예시는 칩 3개. 칩 글은 서버 제목의 쉼표 앞(전체 제목은 화면 읽기 이름에) */
    var ex = clear($('examples'));
    arr(o.examples).slice(0, 3).forEach(function (e) {
      var title = (e && (e.title || e.text)) || txt(e);
      var short = String(title).split(',')[0].trim();
      var btn = el('button', { type: 'button', class: 'chip', 'aria-pressed': 'false', 'aria-label': '예시: ' + title },
        icon('search'), el('span', { text: short }), el('span', { class: 'sr-only chip-sr', text: '' }));
      btn.addEventListener('click', function () { useExample(e, btn); });
      ex.appendChild(btn);
    });
    ex.hidden = !ex.firstChild;
  }

  function markExample(btn) {
    Array.prototype.forEach.call(document.querySelectorAll('#examples .chip'), function (b) {
      var on = b === btn;
      b.classList.toggle('picked', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  function setSelect(sel, value) {
    if (value === undefined || value === null) return;
    var want = optValue(value);
    for (var i = 0; i < sel.options.length; i++) {
      if (sel.options[i].value === want || sel.options[i].textContent === want) { sel.selectedIndex = i; return; }
    }
  }

  /* 예시는 칸을 채우고 버튼에 초점만 준다(누르자마자 보내지 않는다). 직접 고르기 칸도 같은 값으로 */
  function useExample(e, btn) {
    var text = (e && e.text) || txt(e);
    $('purposeText').value = text;
    markExample(btn || null);
    if (e && e.slots && typeof e.slots === 'object') {
      state.pickFor = state.run;           /* 예시 값이 직접 고르기 칸에 남게(지난 결과로 덮지 않는다) */
      setSelect($('slotWho'), e.slots.who);
      setSelect($('slotWhat'), e.slots.what);
      setSelect($('slotWhen'), e.slots.when);
      setSelect($('slotRegion'), e.slots.region);
      var want = arr(e.slots.units).map(optValue);
      Array.prototype.forEach.call(document.querySelectorAll('#slotUnits input'), function (c) { c.checked = want.indexOf(c.value) >= 0; });
    }
    $('planError').hidden = true;
    $('planBtn').focus();
    say('예시 문장을 넣었어요. 받을 목록 만들기를 누르세요.');
  }

  function readSlots() {
    var units = [];
    Array.prototype.forEach.call(document.querySelectorAll('#slotUnits input:checked'), function (c) { units.push(c.value); });
    return { who: $('slotWho').value, what: $('slotWhat').value, when: $('slotWhen').value, region: $('slotRegion').value, units: units };
  }

  function selectedLabel(sel) { return sel.value ? sel.options[sel.selectedIndex].textContent : ''; }

  function slotsSummary() {
    var parts = [];
    var pairs = [['지역', $('slotRegion')], ['누구', $('slotWho')], ['무엇', $('slotWhat')], ['언제', $('slotWhen')]];
    pairs.forEach(function (p) { var l = selectedLabel(p[1]); if (l) parts.push(p[0] + ' ' + l); });
    var units = [];
    Array.prototype.forEach.call(document.querySelectorAll('#slotUnits input:checked'), function (c) { units.push(c.parentNode.textContent); });
    if (units.length) parts.push('함께 나눠 볼 기준 ' + units.join(', '));
    return parts.join(' / ');
  }

  /* trace 의 덧붙임 "… '시간대' 단위를 뺐다" 에서 뺀 기준 이름을 모은다 */
  function droppedUnits(data) {
    var out = [];
    list(data.trace).forEach(function (t) {
      var re = /'([^']+)' 단위를 뺐다/g, m;
      var s = txt(t.note);
      while ((m = re.exec(s))) if (out.indexOf(m[1]) < 0) out.push(m[1]);
    });
    return out;
  }

  /* 직접 고르기 칸을 이번 응답의 읽은 값(지역·업종·누구·기준)으로 채운다(앞서 누른 예시 값이 남지 않게) */
  function prefillSlots(need, extra) {
    need = need || {};
    ['slotWho', 'slotWhat', 'slotWhen', 'slotRegion'].forEach(function (id) { $(id).selectedIndex = 0; });
    if (need.who_key) setSelect($('slotWho'), need.who_key);
    var biz = need.biz_detail && need.biz_detail.code;
    if (need.subject === 'visit') setSelect($('slotWhat'), 'pop');
    else if (biz) setSelect($('slotWhat'), 'biz:' + biz);
    var rd = need.region_detail || {};
    [rd.bjd, rd.emd, rd.district7, need.region].some(function (v) {
      if (!v) return false;
      setSelect($('slotRegion'), v);
      return !!$('slotRegion').value;
    });
    var keys = list(need.units).map(function (u) { return txt(u.key); });
    var labels = arr(extra);
    var slot = keys.indexOf('slot') >= 0 || labels.indexOf('시간대') >= 0;
    var dow = keys.indexOf('dow') >= 0 || labels.indexOf('요일') >= 0;
    setSelect($('slotWhen'), slot && dow ? 'dow_slot' : (slot ? 'slot' : (dow ? 'dow' : '')));
    Array.prototype.forEach.call(document.querySelectorAll('#slotUnits input'), function (c) {
      var lab = c.parentNode.textContent;
      c.checked = keys.indexOf(c.value) >= 0 || labels.indexOf(lab) >= 0;
    });
  }

  /* 직접 고르기 페이지로 가서 그 칸에 초점(경고·빠진 기준에서 고치러 갈 때) */
  function slotFocus(kind, extra) {
    if (state.run) { prefillSlots(state.run.need, extra); state.pickFor = state.run; }
    go('/pick');
    var target = kind === 'biz' ? $('slotWhat') : ((kind === 'region' || kind === 'place') ? $('slotRegion') : (kind === 'when' ? $('slotWhen') : $('slotWho')));
    target.focus();
    say(extra && extra.length ? '직접 고르기 칸에 지금 질문의 값을 넣고 ' + extra.join('·') + '도 골랐어요. 받을 목록 만들기를 누르세요.'
      : '직접 고르기 화면이에요. ' + (target.labels && target.labels[0] ? target.labels[0].textContent : '') + ' 칸을 골라 주세요.');
  }

  /* ---------- 불러오는 중 ---------- */

  var progressTimer = null;
  function startProgress(isLlm) {
    var began = Date.now(), tau = isLlm ? 6000 : 700;
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var fill = $('loadFill');
    fill.style.width = reduce ? '20%' : '0%';
    var longSaid = false;
    var longMsg = '오래 걸리고 있어요. AI 응답이 없으면 AI 없이 만들어요.';
    var tick = function () {
      var ms = Date.now() - began, sec = Math.floor(ms / 1000);
      if (!reduce) fill.style.width = (90 * (1 - Math.exp(-ms / tau))).toFixed(1) + '%';
      $('loadTime').textContent = sec > 0 ? sec + '초 지났어요' + (sec >= 90 ? '. ' + longMsg : '') : '';
      if (sec >= 90 && !longSaid) { longSaid = true; say(longMsg); }
    };
    tick();
    progressTimer = setInterval(tick, 250);
  }
  function stopProgress() {
    if (progressTimer) clearInterval(progressTimer);
    progressTimer = null;
    $('loadFill').style.width = '100%';
  }

  function setBusy(on) {
    state.busy = on;
    if (!on) $('busyBand').hidden = true;
    $('planBtn').disabled = on;
    $('pickBtn').disabled = on;
    $('purposeText').readOnly = on;
    Array.prototype.forEach.call(document.querySelectorAll('#examples button, #pickForm select, #pickForm input'), function (b) { b.disabled = on; });
  }

  function submitPlan(kind) {
    if (state.busy) return;
    var errBox = kind === 'text' ? $('planError') : $('pickError');
    errBox.hidden = true;
    var body, purpose;
    if (kind === 'text') {
      var text = $('purposeText').value.trim();
      if (!text) {
        showError(errBox, { message: '궁금한 것이 비어 있어요.', action: '한 문장으로 적거나 예시를 눌러 주세요.' });
        $('purposeText').focus();
        return;
      }
      body = { mode: 'auto', text: text, slots: { who: '', what: '', when: '', region: '', units: [] } };
      purpose = text;
    } else {
      var slots = readSlots();
      if (!slots.who && !slots.what && !slots.when && !slots.region) {
        showError(errBox, { message: '아무것도 고르지 않았어요.', action: '누구·무엇을·언제·지역 가운데 하나를 골라 주세요.' });
        $('slotWho').focus();
        return;
      }
      body = { mode: 'rule', text: '', slots: slots };
      purpose = slotsSummary();
    }
    state.origin = kind === 'text' ? '/home' : '/pick';
    $('loadTitle').textContent = kind === 'text' ? '질문을 읽고 있어요' : '목록을 만들고 있어요';
    setBusy(true);
    go('/loading');
    startProgress(kind === 'text' && llmOn());
    api('POST', '/api/plan', JSON.stringify(body), { 'Content-Type': 'application/json; charset=utf-8' }).then(function (data) {
      state.run = data;
      state.purpose = purpose;
      state.inputKind = kind;
      state.rendered = null;
      resetCsvResults();
      hideGlobalMsg();
      stopProgress();
      setBusy(false);
      /* 기다리는 동안 다른 페이지로 옮겨 갔으면 갑자기 넘기지 않고 띠로 알린다 */
      if (state.path === '/loading') go('/list', { replace: true });
      else {
        state.readyNote = true;
        showMsg('ok', '받을 목록이 준비됐어요.', '/list', '목록 보기 ›');
        say('받을 목록이 준비됐어요. 화면 위의 목록 보기를 누르세요.');
      }
    }, function (err) {
      stopProgress();
      setBusy(false);
      var away = state.path !== '/loading' && state.path !== state.origin;
      if (state.path === '/loading') go(state.origin, { replace: true });
      showError(away ? $('globalError') : errBox, err);
      if (!away) (kind === 'text' ? $('planBtn') : $('pickBtn')).focus({ preventScroll: true });
    });
  }

  /* ---------- 페이지 전환 (주소 #/…) ---------- */

  var ROUTES = [
    [/^\/home$/, 'home'], [/^\/pick$/, 'pick'], [/^\/help$/, 'help'], [/^\/status$/, 'status'],
    [/^\/loading$/, 'loading'], [/^\/list$/, 'list'],
    [/^\/detail\/(data|months|conditions|public|nodata|oos|trust)$/, 'detail'],
    [/^\/alt$/, 'alt'], [/^\/trust\/(check|ai|sent|export|limits)$/, 'trust'],
    [/^\/file$/, 'file'], [/^\/file\/(\d+)$/, 'fileK'], [/^\/file\/detail\/(\d+)$/, 'fileDetail'],
    [/^\/file\/detail\/(\d+)\/(numbers|compare)$/, 'fileSub'],
    [/^\/file\/wait$/, 'fileWait'], [/^\/file\/practice$/, 'filePractice']
  ];
  var PAGE_OF = { home: 'page-home', pick: 'page-pick', help: 'page-help', loading: 'page-loading', list: 'page-list', file: 'page-file', fileK: 'page-file' };
  var DOT_OF = { home: 1, pick: 1, help: 1, status: 1, loading: 2, list: 2, detail: 2, alt: 2, trust: 2 };
  var NEED_RUN = { list: 1, detail: 1, alt: 1, trust: 1 };

  function resolve(path) {
    for (var i = 0; i < ROUTES.length; i++) {
      var m = ROUTES[i][0].exec(path);
      if (m) {
        var kind = ROUTES[i][1];
        return { kind: kind, arg: m[1], arg2: m[2], page: PAGE_OF[kind] || 'page-detail', dot: DOT_OF[kind] || 3, needRun: !!NEED_RUN[kind] };
      }
    }
    return null;
  }

  /* 돌아가기의 부모 주소. 바로 앞 화면이 기록에 없을 때(주소를 직접 열었을 때)만 쓴다 */
  function parentOf(path) {
    var r = resolve(path);
    if (!r) return '/home';
    if (r.kind === 'pick' || r.kind === 'help' || r.kind === 'status') return '/home';
    if (r.kind === 'list') return state.origin === '/pick' ? '/pick' : '/home';
    if (r.kind === 'detail' || r.kind === 'alt') return '/list';
    if (r.kind === 'trust') return '/detail/trust';
    if (r.kind === 'file') return state.run ? '/list' : '/home';
    if (r.kind === 'fileK' || r.kind === 'fileWait' || r.kind === 'filePractice') return '/file';
    if (r.kind === 'fileDetail') return state.csv.length > 1 ? '/file/' + r.arg : '/file';
    if (r.kind === 'fileSub') return '/file/detail/' + r.arg;
    return '/home';
  }

  function pathFromHash() {
    var h = location.hash || '';
    return h.indexOf('#/') === 0 ? h.slice(1) : null;
  }

  function go(path, opt) {
    opt = opt || {};
    var seq = state.navSeq;
    try {
      if (opt.replace) history.replaceState({ path: path, seq: seq }, '', '#' + path);
      else { seq = ++state.navSeq; history.pushState({ path: path, seq: seq }, '', '#' + path); }
    } catch (e) { /* 파일로 열었을 때 등 */ }
    state.stack = state.stack || [];
    state.stack.length = seq;
    state.stack[seq] = path;
    render(path, opt.replace ? 'replace' : 'push');
  }

  /* "← 돌아가기": 브라우저 뒤로 가기와 같게, 바로 앞 화면이 기록에 있으면 그리로 돌아간다.
     앞 화면이 없을 때(주소를 직접 열었을 때)만 부모 주소로 바꾼다 */
  function goBack(fallback) {
    var seq = (history.state && history.state.seq) || 0;
    var prev = state.stack && seq > 0 ? state.stack[seq - 1] : null;
    if (prev && resolve(prev)) { history.back(); return; }
    go(fallback || parentOf(state.path), { replace: true });
  }

  function hideGlobalMsg() { $('globalMsg').hidden = true; }

  /* 머리 아래 한 줄 알림(kind: ok 초록, info 회색). href 가 있으면 그 페이지로 가는 링크를 붙인다 */
  function showMsg(kind, text, href, linkText) {
    var msg = clear($('globalMsg'));
    msg.className = 'band ' + (kind === 'info' ? 'band-info' : 'band-ok');
    append(msg, [icon(kind === 'info' ? 'info' : 'check-circle'), el('span', { class: 'band-text', text: text }),
      href ? el('a', { class: 'link', href: '#' + href }, linkText || '보기 ›') : null]);
    msg.hidden = false;
  }

  var SAY_NAME = { '/loading': '받을 목록을 만들고 있어요.' };

  function render(path, how) {
    var r = resolve(path);
    if (!r) { go('/home', { replace: true }); return; }
    if (r.needRun && !state.run) { go('/home', { replace: true }); showMsg('info', '보던 결과가 없어 처음 화면으로 왔어요.'); return; }
    if (r.needRun && !applies(r)) { go('/list', { replace: true }); return; }
    if (r.kind === 'loading' && !state.busy) { go(state.run ? '/list' : '/home', { replace: true }); return; }
    if ((r.kind === 'fileK' || r.kind === 'fileDetail' || r.kind === 'fileSub') && !state.csv[Number(r.arg) - 1]) {
      go('/file', { replace: true });
      if (!state.csv.length) showMsg('info', '넣은 파일 결과가 없어 받은 파일 화면으로 왔어요.');
      return;
    }
    if (how !== 'force' && path === state.path && !$(r.page).hidden) return;
    var prevPath = state.path;
    state.path = path;
    $('chartTip').hidden = true;
    if (prevPath && path !== '/home') hideGlobalMsg();
    /* 목록을 만드는 중에 다른 페이지로 갔으면 머리 아래 띠로 알린다. 다 됐으면 목록에 갈 때까지 알림을 남긴다 */
    $('busyBand').hidden = !(state.busy && r.kind !== 'loading');
    if (r.kind === 'list') state.readyNote = false;
    else if (state.readyNote && state.run) showMsg('ok', '받을 목록이 준비됐어요.', '/list', '목록 보기 ›');

    Array.prototype.forEach.call(document.querySelectorAll('main > .page'), function (p) { p.hidden = p.id !== r.page; p.classList.remove('enter'); });
    var page = $(r.page);
    void page.offsetWidth;
    page.classList.add('enter');

    RENDER[r.kind](r.arg, path, r.arg2);
    setDots(r.dot);

    var h1 = page.querySelector('h1');
    var name = h1 ? h1.textContent : '';
    document.title = name + ' - 센터 방문 주문서';
    window.scrollTo(0, 0);
    var target = null;
    if (how === 'pop' && state.lastTrigger[path]) {
      target = page.querySelector('a[href="' + state.lastTrigger[path] + '"], [data-trigger="' + state.lastTrigger[path] + '"]');
    }
    if (target && target.offsetParent !== null) target.focus({ preventScroll: true });
    else if (h1) h1.focus({ preventScroll: true });
    say(SAY_NAME[path] || (name + ' 화면이에요.'));
  }

  /* 이번 결과에 그 상세 페이지가 있는가(없는 페이지를 주소로 열면 받을 목록으로 돌려보낸다) */
  function applies(r) {
    var o = state.run.order || {}, items = list(o.items);
    var key = r.kind === 'alt' ? 'alt' : r.arg;
    if (key === 'data' || key === 'months' || key === 'conditions' || key === 'export') return items.length > 0 && !o.out_of_scope;
    if (key === 'nodata') return list(o.no_data).length > 0;
    if (key === 'oos') return !!o.out_of_scope;
    if (key === 'alt') return list(o.alternatives).length > 0;
    if (key === 'public') return !o.out_of_scope;
    if (key === 'limits') return arr(state.run.limits).filter(Boolean).length > 0;
    return true;
  }

  function setDots(n) {
    [1, 2, 3].forEach(function (k) { $('dot' + k).classList.toggle('on', k === n); });
    $('dotsSr').textContent = '3단계 중 ' + n + '단계: ' + ['질문', '받을 목록', '받은 파일'][n - 1];
  }

  /* 상세 페이지 공통 틀에 그린다 */
  function setDetail(o) {
    setIllu($('detailIllu'), o.illu || null);
    $('detailTitle').textContent = o.title || '';
    $('detailSub').textContent = o.sub || '';
    var body = clear($('detailBody'));
    append(body, arr(o.body));
    fillIcons(body);
    var back = $('detailBack');
    back.setAttribute('href', '#' + (o.back || parentOf(state.path)));
    back.lastChild.textContent = o.backText || '돌아가기';
  }

  /* 근거 원문 상자(접힘). 개발자 글자(명세서 번호·컬럼 영문·코드)는 이 안에서만 보인다 */
  function rawBox(kids, label) {
    var body = el('div', { class: 'raw-body' });
    append(body, arr(kids));
    if (!body.firstChild) return null;
    /* 안에 h3 가 있으면 제목 단계가 건너뛰지 않게 화면 읽기용 h2 를 앞에 둔다 */
    if (body.querySelector('h3')) body.insertBefore(el('h2', { class: 'sr-only', text: label || '근거 원문' }), body.firstChild);
    return el('details', { class: 'raw' }, el('summary', { text: label || '근거 원문' }), body);
  }
  function rawList(lines, head) {
    var ls = arr(lines).map(txt).filter(Boolean);
    if (!ls.length) return null;
    return [head ? el('h3', { text: head }) : null, el('ul', null, ls.map(function (s) { return el('li', { text: s }); }))];
  }
  function rawLine(k, v) {
    var t = txt(v);
    return t ? el('p', null, el('b', { text: k + ': ' }), t) : null;
  }

  /* 목록 슬립 한 줄. href 가 있으면 누를 수 있는 행(›), 없으면 읽기 전용 행 */
  function slipRow(label, value, href, opt) {
    opt = opt || {};
    var inner = [
      opt.lead ? icon(opt.lead, 'lead-ico ' + (opt.leadCls || '')) : null,
      el('span', { class: 'slip-text' },
        label ? el('span', { class: 'slip-label' }, label) : null,
        el('span', { class: 'slip-value' }, value),
        opt.note || null),
      href ? el('span', { class: 'slip-chev', 'aria-hidden': 'true' }, icon('chevron-right')) : null];
    var row = href ? el('a', { class: 'slip-row' + (opt.cls ? ' ' + opt.cls : ''), href: '#' + href }, inner)
      : el('div', { class: 'slip-row' + (opt.cls ? ' ' + opt.cls : '') }, inner);
    return el('li', null, row);
  }
  function kvRow(k, v) {
    return el('li', null, el('div', { class: 'slip-row' }, el('div', { class: 'kv' }, el('span', { class: 'kv-k', text: k }), el('span', { class: 'kv-v' }, v))));
  }

  /* ---------- 받을 목록 ---------- */

  function distinctMonths(items) {
    var seen = {}, out = [];
    items.forEach(function (it) { list(it && it.months).forEach(function (m) { var k = txt(m.ym); if (k && !seen[k]) { seen[k] = 1; out.push(k); } }); });
    return out;
  }

  function totalDownloads(order, items) {
    return isNum(order.total_downloads) ? order.total_downloads : items.reduce(function (a, it) { return a + (isNum(it.downloads) ? it.downloads : 0); }, 0);
  }

  /* 고를 조건의 화면 이름(코드는 근거 원문과 인쇄본에만) */
  function condName(c) {
    var col = String(c.column || '');
    if (col === 'bjd' || col === 'bjd_nm' || col === 'emd_nm' || col === 'emd' || col === 'district7' || col === 'city') return { k: '지역', ic: 'pin' };
    if (col === 'biz_cd') return { k: '업종', ic: 'store' };
    if (col === 'biz_small_cd' || col === 'biz_mid_cd') return { k: '업종(세부)', ic: 'store' };
    if (/user|tour|type/.test(col)) return { k: '이용자 구분', ic: 'users' };
    return { k: txt(c.column_ko) || '조건', ic: 'layers' };
  }
  function condValue(c) { return txt(c.value_label) || txt(c.value); }

  function exportStatement(ex) {
    if (!ex) return '';
    if (ex.allowed === false) return '원본 파일은 센터 밖으로 가져갈 수 없어요. 비율·순위처럼 가공한 결과만 반출 신청할 수 있어요.';
    if (ex.allowed === true) return '센터 밖으로 가져갈 수 있어요(반출 규칙 확인).';
    var s = txt(ex.allowed);
    return s ? '반출: ' + s : '';
  }

  /* 화면에서 이름을 쓸 수 있는 줄은 "1-12: …" 의 번호를 데이터 이름으로 바꿔 보인다(원문은 근거 원문 안) */
  function nameByNo(items) {
    var map = {};
    arr(items).forEach(function (it) { if (it && it.dataset_no) map[it.dataset_no] = dsName(it.dataset_name); });
    return function (s) {
      var m = /^(\d+-\d+)\s*:\s*(.*)$/.exec(s);
      return (m && map[m[1]]) ? map[m[1]] + ': ' + m[2] : s;
    };
  }
  /* "나안: 한 종으로 함께 보기 (1-22)" -> "한 종으로 함께 보기" */
  function altTitle(t) { return String(t || '').replace(/\s*\([^()]*\d+-\d+[^()]*\)\s*$/, '').replace(/^[가-힣]안\s*:\s*/, ''); }

  function unverifiedConds(items) {
    var out = [];
    items.forEach(function (it) { list(it && it.conditions).forEach(function (c) { if (c && c.evidence && c.evidence.verified === false) out.push(c); }); });
    return out;
  }

  function wholeIslandNote(items) {
    var all = [];
    items.forEach(function (it) { arr(it.month_notes).concat(arr(it.months_note)).forEach(function (s) { all.push(txt(s)); }); });
    return all.some(function (s) { return /도 전체/.test(s); });
  }

  function listNotes(data) {
    var box = el('div', { class: 'list-notes' });
    var order = data.order || {};
    /* 경고(직접 고르기로 고치기) */
    var warns = arr(data.warnings).filter(Boolean);
    if (warns.length) {
      var rows = warns.map(function (w) {
        var t = firstSentence(easy(w && w.text ? w.text : w));
        return el('li', null, el('span', { text: t + ' ' }),
          (w && w.action === 'slots') ? el('button', { type: 'button', class: 'link', onclick: function () { slotFocus(w.kind); } }, '직접 고르기로 고치기 ›') : null);
      });
      box.appendChild(el('div', { class: 'mini-band warn' }, icon('alert'), rows.length > 1 ? el('ul', { class: 'band-text' }, rows) : el('div', { class: 'band-text' }, rows[0].childNodes[0], rows[0].childNodes[1] || null)));
    }
    /* 대조 어긋남 */
    var v = data.validation || {};
    if (isNum(v.total) && isNum(v.passed) && v.total - v.passed > 0) {
      box.appendChild(el('div', { class: 'mini-band bad' }, icon('x-circle'),
        el('div', null, '설명서와 맞지 않는 줄이 있어 뺐어요. ', el('a', { class: 'link', href: '#/trust/check' }, '보기 ›'))));
    }
    /* 문장에 없어 뺀 기준 */
    var dropped = droppedUnits(data);
    if (dropped.length && !order.out_of_scope) {
      var words = dropped.join('·');
      box.appendChild(el('div', { class: 'mini-band' }, icon('info'),
        el('div', null, josa(words, '은는') + ' 문장에 없어 뺐어요. ', el('button', { type: 'button', class: 'link', onclick: function () { slotFocus('when', dropped); } }, '넣어서 다시 만들기 ›'))));
    }
    /* AI 없이 만든 목록 */
    if (data.mode_used !== 'llm') {
      var fb = txt(data.fallback_reason);
      var t = order.out_of_scope ? 'AI 없이 판단했어요.' : (fb && state.inputKind === 'text' ? 'AI를 쓰지 못해 AI 없이 만들었어요.' : 'AI 없이 만든 목록이에요.');
      /* 문장으로 만든 목록이면 읽은 값을 채운 직접 고르기로 다시 만들 수 있게 한다(2차의 "직접 고르기로 다시 만들기") */
      var redo = state.inputKind !== 'slots' && !order.out_of_scope
        ? el('button', { type: 'button', class: 'link', onclick: function () { slotFocus('who'); } }, '직접 고르기로 다시 만들기 ›') : null;
      box.appendChild(el('div', { class: 'mini-band' }, icon('info'),
        el('div', null, t + ' ', el('a', { class: 'link', href: '#/trust/ai' }, '까닭 ›'), redo ? ' ' : null, redo)));
    }
    return box;
  }

  function actions(kids) { return el('div', { class: 'actions' }, kids); }
  function linkRow(text, href, onclick) {
    return el('p', { class: 'link-row' }, href ? el('a', { class: 'link', href: '#' + href }, text) : el('button', { type: 'button', class: 'link', onclick: onclick }, text));
  }
  function printBtn(text, main) {
    return el('button', { type: 'button', class: main ? 'btn-main' : 'btn-sub', onclick: function () { window.print(); } }, icon('printer'), text);
  }

  function renderList() {
    var data = state.run;
    if (state.rendered === data) return;
    state.rendered = data;
    NAME_MAP = {};
    var order = data.order || {}, items = list(order.items), alts = list(order.alternatives), nod = list(order.no_data);
    rememberNames(items);
    alts.forEach(function (a) { rememberNames(a.items); });
    if (order.out_of_scope) rememberNames(order.out_of_scope.datasets);

    var body = clear($('listBody'));
    var illuName = 'list-ok', title = '이렇게 받으시면 돼요', sub = '', long = false;

    if (order.out_of_scope) {
      var oos = order.out_of_scope;
      illuName = 'oos';
      title = '질문을 조금 바꿔 볼까요?';
      sub = txt(oos.topic) ? txt(oos.topic) + '에 관한 질문은 목록을 만들 수 없어요.' : '이 질문으로는 받을 목록을 만들 수 없어요.';
      append(body, [listNotes(data), actions([el('a', { class: 'btn-main', href: '#/home' }, '질문 다시 하기'),
        list(oos.datasets).length ? linkRow('센터에 있는 비슷한 자료 이름 보기 ›', '/detail/oos') : null])]);
    } else if (!items.length) {
      var note = txt(order.note);
      if (note) {
        title = '센터에서 받을 것이 없어요';
        sub = /공개 데이터에 있어/.test(note) ? '필요한 정보가 모두 공개 데이터에 있어요.' : easy(note);
        append(body, [listNotes(data), actions([el('a', { class: 'btn-main', href: '#/detail/public' }, '공개 데이터로 보기'),
          el('a', { class: 'btn-sub', href: '#/home' }, '질문 다시 하기'), linkRow('안내 인쇄하기', null, function () { window.print(); })])]);
      } else if (nod.length) {
        illuName = 'none';
        title = '이 정보는 센터에 없어요';
        var inst0 = arr(nod[0].instead).map(txt).filter(Boolean)[0];
        sub = inst0 ? firstSentence(easy(dropLead(inst0))) : firstSentence(easy(nod[0].explain));
        append(body, [listNotes(data), actions([el('a', { class: 'btn-main', href: '#/detail/nodata' }, '대신 볼 것 보기'),
          el('a', { class: 'btn-sub', href: '#/home' }, '질문 다시 하기')])]);
      } else {
        illuName = 'none';
        title = '센터에서 받을 것이 없어요';
        sub = '설명서와 맞춰 본 항목이 없어요.';
        append(body, [listNotes(data), actions([el('a', { class: 'btn-main', href: '#/home' }, '질문 다시 하기'),
          linkRow('이 결과를 믿어도 될까요?', '/detail/trust')])]);
      }
    } else {
      /* 받는 횟수는 서버 값(total_downloads, 없으면 자료별 downloads 합) */
      var dlN = totalDownloads(order, items);
      var dlText = dlN > 0 ? '한 달 치씩 ' + fmtCount(dlN, '번') + ' 받으면 돼요.' : '한 달 치씩 나눠 받아요.';
      sub = alts.length ? '두 가지 방법 중 추천하는 방법이에요.' : (items.length > 1 ? '자료 ' + items.length + '가지를 ' + dlText : '센터에서 ' + dlText);
      var first = dsName(items[0].dataset_name);
      var whatText = items.length > 1 ? first + ' 외 ' + (items.length - 1) + '가지' : first;
      var months = distinctMonths(items);
      var monthText = months.length > 3 ? months.slice(0, 2).map(fmtYmKo).join(' · ') + ' 외 ' + (months.length - 2) + '달' : months.map(fmtYmKo).join(' · ');
      var condVals = [];
      items.forEach(function (it) { list(it.conditions).forEach(function (c) { var v = condValue(c); if (v && condVals.indexOf(v) < 0) condVals.push(v); }); });
      var unv = unverifiedConds(items).length > 0 || (isNum((data.validation || {}).unverified) && data.validation.unverified > 0);
      var rows = [
        slipRow(alts.length ? el('span', null, '무엇을 받나요? ', el('span', { class: 'rec-badge' }, icon('check'), '추천 방법')) : '무엇을 받나요?', whatText, '/detail/data'),
        slipRow('몇 월 치를 받나요?', monthText || '받을 달을 정하지 못했어요', '/detail/months'),
        slipRow('어떤 조건으로 받나요?', condVals.length ? condVals.join(' · ') : '조건 없이 전체', '/detail/conditions',
          { note: unv ? el('span', { class: 'slip-note' }, icon('alert'), '센터 화면에서 확인할 것이 있어요') : null })];
      if (alts.length) {
        var other = alts.filter(function (a) { return a && !a.recommended; })[0] || alts[0];
        rows.push(slipRow('다른 방법 보기', plain(altTitle(other.title)), '/alt'));
      }
      if (nod.length) {
        var units = [];
        nod.forEach(function (n) { var u = txt(n.unit) || '이 정보'; if (units.indexOf(u) < 0) units.push(u); });
        rows.push(slipRow('', josa(units.join('·'), '은는') + ' 센터에도 없어요', '/detail/nodata', { lead: 'none-circle', leadCls: 'warn', cls: 'caution' }));
      }
      var wrap = el('div', { class: 'slip-wrap' }, el('ul', { class: 'slip', 'aria-label': '받을 목록' }, rows));
      long = rows.length > 3;
      append(body, [listNotes(data), wrap, actions([printBtn('목록 인쇄하기', true),
        el('a', { class: 'btn-sub', href: '#/file' }, '받은 파일 넣기'),
        linkRow('샘플 파일로 3단계 보기 ›', null, function () { useSampleCsv(); }),
        linkRow('이 결과를 믿어도 될까요?', '/detail/trust')]), localBox(items)]);
    }
    /* 줄이 많으면 낮은 화면(1366×768)에서 그림을 접어 주 버튼이 첫 화면에 남게 한다 */
    $('page-list').classList.toggle('long', long);
    /* 직접 고르기로 만든 목록이면 돌아가기도 고르는 화면으로 */
    var lb = $('listBack');
    lb.setAttribute('href', state.origin === '/pick' ? '#/pick' : '#/home');
    lb.lastChild.textContent = state.origin === '/pick' ? '고른 것 고치기' : '질문 고치기';
    setIllu($('listIllu'), illuName);
    $('listTitle').textContent = title;
    $('listSub').textContent = sub;
    fillIcons(body);
    renderPrintSheet(data);
  }

  /* ---------- 상세: 무엇을 받나요 ---------- */

  function colNames(it) {
    return list(it.use_columns).map(function (c) { return (c !== null && typeof c === 'object') ? txt(c.ko || c.en) : txt(c); }).filter(Boolean);
  }

  function itemHead(items, i) {
    return items.length > 1 ? el('h2', { class: 'h2', text: '자료 ' + (i + 1) + ' · ' + dsName(items[i].dataset_name) }) : null;
  }

  function renderDetailData() {
    var data = state.run, order = data.order || {}, items = list(order.items);
    var gaps = list(data.gaps);
    var bodyKids = [];
    var raw = [];
    items.forEach(function (it, i) {
      var fills = gaps.filter(function (g) { return arr(g.by).indexOf(it.dataset_no) >= 0 && txt(g.status).indexOf('없') < 0; }).map(function (g) { return txt(g.label || g.key); });
      var cols = colNames(it);
      var rows = [];
      if (it.provider) rows.push(kvRow('제공', txt(it.provider)));
      rows.push(kvRow('담긴 기간', ymInText(plain(it.period_text)) || '명세서에 없어요'));
      if (cols.length) rows.push(kvRow('들어 있는 항목', el('span', { class: 'tags' }, cols.map(function (c) { return el('span', { class: 'tag', text: c }); }))));
      if (fills.length) rows.push(kvRow('이 자료로 채우는 것', fills.join(' · ')));
      if (isNum(it.downloads)) rows.push(kvRow('받는 횟수', fmtCount(it.downloads, '번') + ' · 한 번에 한 달 치씩'));
      if (it.caveat) rows.push(kvRow('알아 둘 점', el('span', { class: 'plain', text: easy(it.caveat) })));
      if (it.why) rows.push(kvRow('고른 까닭', el('span', { class: 'plain', text: easy(it.why) })));
      bodyKids.push(itemHead(items, i), el('ul', { class: 'slip read' }, rows));
      raw.push(el('h3', { text: (items.length > 1 ? '자료 ' + (i + 1) + ': ' : '') + txt(it.dataset_no) + ' ' + txt(it.dataset_name) }),
        rawLine('제공', it.provider), rawLine('수록', it.period_text), rawLine('고른 까닭', it.why), rawLine('알아 둘 점', it.caveat),
        rawLine('받을 항목', list(it.use_columns).map(function (c) { return (c && typeof c === 'object') ? txt(c.ko) + '(' + txt(c.en) + ')' : txt(c); }).join(', ')),
        rawList(it.evidence, '명세서 근거'),
        rawLine('받은 파일로 다시 계산', it.reanalysis === true ? '할 수 있음' : (it.reanalysis === false ? '할 수 없음' : '')));
    });
    bodyKids.push(el('ul', { class: 'slip' }, slipRow('', '공개 데이터로 먼저 본 것', '/detail/public', { lead: 'layers' })));
    bodyKids.push(rawBox(raw));
    setDetail({ illu: 'folder', title: items.length > 1 ? '자료 ' + items.length + '가지를 받아요' : dsName(items[0].dataset_name),
      sub: '센터 화면에서 이 이름을 찾아 받으세요.', body: bodyKids });
  }

  /* ---------- 상세: 왜 이 달인가요 ---------- */

  function stripRef(role) { return txt(role).replace(/^참고\s*:\s*/, ''); }

  function renderDetailMonths() {
    var data = state.run, order = data.order || {}, items = list(order.items), pa = data.public_answer || {};
    var it0 = items[0] || {}, ms = list(it0.months);
    var names = ms.map(function (m) { var p = parseYm(m.ym); return p ? p.m + '월' : fmtYmKo(m.ym); });
    var title = names.length ? names.join('·').replace(/([^·]+)$/, function (w) { return josa(w, '을를'); }) + ' 골랐어요' : '받을 달을 정하지 못했어요';
    var roles = ms.map(function (m) { return stripRef(m.role); });
    var three = ['많', '적', '평균'].every(function (k) { return roles.some(function (r) { return r.indexOf(k) >= 0; }); });
    var isRef = ms.some(function (m) { return /^참고/.test(txt(m.role)); });
    var kids = [];
    if (isRef) {
      var mn0 = arr(it0.month_notes).map(txt).filter(Boolean)[0];
      if (mn0) kids.push(el('div', { class: 'mini-band warn' }, icon('alert'), el('div', { text: easy(mn0) })));
    }
    var picked = pickedMonths(order);
    var chart = null;
    if (pa.available !== false && arr(pa.seasonal).length) chart = seasonChart(pa.seasonal, picked);
    else if (pa.reference && typeof pa.reference === 'object' && arr(pa.reference.seasonal).length) chart = seasonChart(pa.reference.seasonal, picked, '참고');
    kids.push(chart || el('p', { class: 'body-text muted', text: '그래프로 볼 공개 데이터가 없어요.' }));
    if (ms.length) {
      kids.push(el('p', { class: 'body-text', text: ms.map(function (m) {
        var p = parseYm(m.ym), nm = p ? p.m + '월' : fmtYmKo(m.ym);
        var w = indexWords(m.index);
        return josa(nm, '은는') + ' ' + stripRef(m.role) + '이에요' + (w ? '(' + w + ')' : '') + '.';
      }).join(' ').replace(/([가-힣])이에요/g, function (mm, c) { return hasBatchim(c) ? mm : c + '예요'; }) }));
    }
    var rows = [kvRow('받는 횟수', '모두 ' + fmtCount(totalDownloads(order, items), '번') + ' · 한 번에 한 달 치씩')];
    if (wholeIslandNote(items)) rows.push(kvRow('알아 둘 점', el('span', { class: 'plain', text: '제주도 전체로 고른 달이라 동네마다 다를 수 있어요.' })));
    items.slice(1).forEach(function (it) {
      var l = list(it.months).map(function (m) { return fmtYmKo(m.ym) + ' ' + stripRef(m.role); }).join(' · ');
      if (l) rows.push(kvRow(dsName(it.dataset_name), l));
    });
    kids.push(el('ul', { class: 'slip read' }, rows));
    kids.push(el('ul', { class: 'slip' }, slipRow('', '공개 데이터로 본 흐름', '/detail/public', { lead: 'layers' })));
    var raw = [];
    items.forEach(function (it) {
      raw.push(el('h3', { text: txt(it.dataset_no) + ' ' + txt(it.dataset_name) }));
      raw.push(rawList(list(it.months).map(function (m) { return fmtYm(m.ym) + ' ' + txt(m.role) + (isNum(m.index) ? ' (지수 ' + fmtNum(m.index, 1) + ')' : '') + ': ' + txt(m.basis); })));
      raw.push(rawList(it.months_note, '받을 달 메모'));
      raw.push(rawList(it.month_notes, '달 메모'));
    });
    raw.push(rawList(pa.notes, '공개 데이터 메모'));
    if (pa.reference && pa.reference.title) raw.push(rawLine('참고 그림', pa.reference.title));
    kids.push(rawBox(raw));
    setDetail({ illu: 'bubble-chart', title: title,
      sub: three ? '많은 달, 적은 달, 평균에 가까운 달을 하나씩 골랐어요.' : '목록에 넣은 달이에요.', body: kids });
  }

  /* ---------- 상세: 어떤 조건인가요 ---------- */

  function renderDetailConditions() {
    var data = state.run, order = data.order || {}, items = list(order.items), need = data.need || {};
    var kids = [];
    var raw = [];
    items.forEach(function (it, i) {
      var conds = list(it.conditions);
      var rows = conds.map(function (c) {
        var ev = c.evidence || {};
        var ok = ev.verified === true, unv = ev.verified === false;
        return el('li', null, el('div', { class: 'slip-row' },
          icon(unv ? 'alert' : (ok ? 'check-circle' : 'info'), 'lead-ico ' + (unv ? 'warn' : (ok ? 'ok' : ''))),
          el('span', { class: 'slip-text' }, el('span', { class: 'slip-label', text: condName(c).k }),
            el('span', { class: 'slip-value', text: condValue(c) }),
            unv ? el('span', { class: 'slip-note', text: '센터 화면에서 이 이름이 맞는지 확인하세요' }) : null,
            el('span', { class: 'sr-only', text: ok ? ' (명세서에서 확인함)' : '' }))));
      });
      if (!rows.length) rows.push(kvRow('조건', '조건 없이 전체를 받아요'));
      kids.push(itemHead(items, i), el('ul', { class: 'slip' }, rows));
      conds.forEach(function (c) {
        var ev = c.evidence || {};
        raw.push(el('p', null, el('code', { text: txt(c.column) }), ' (' + txt(c.column_ko) + ') = ', el('code', { text: txt(c.value) }),
          c.value_label ? ' (' + txt(c.value_label) + ')' : '', ev.verified === false ? ' - 명세서로 확인 못 함' : ''));
        if (ev.note || ev.sheet) raw.push(el('p', { text: '근거: 명세서 ' + txt(ev.sheet) + ' 시트 ' + txt(ev.col_no) + '번 컬럼. ' + txt(ev.note) }));
      });
    });
    var read = [];
    if (txt(need.region)) read.push(el('span', { class: 'tag fill' }, icon('pin'), '지역 ' + txt(need.region)));
    if (txt(need.biz)) read.push(el('span', { class: 'tag fill' }, icon('store'), '업종 ' + txt(need.biz)));
    if (txt(need.who)) read.push(el('span', { class: 'tag fill' }, icon('users'), '대상 ' + txt(need.who)));
    var qRows = [];
    if (state.purpose) qRows.push(kvRow(state.inputKind === 'slots' ? '내가 고른 것' : '내 질문', el('span', { class: 'plain', text: state.purpose })));
    if (read.length) qRows.push(kvRow('질문에서 읽은 것', el('span', { class: 'tags' }, read)));
    if (qRows.length) kids.push(el('h2', { class: 'h2', text: '질문을 이렇게 읽었어요' }), el('ul', { class: 'slip read' }, qRows));
    var dropped = droppedUnits(data);
    if (dropped.length) {
      var words = dropped.join('·');
      kids.push(el('div', { class: 'mini-band' }, icon('info'), el('div', null, josa(words, '은는') + ' 문장에 없어 넣지 않았어요. ',
        el('button', { type: 'button', class: 'link', onclick: function () { slotFocus('when', dropped); } }, '넣어서 다시 만들기 ›'))));
    }
    raw.push(rawList(need.notes, '문장 풀이 근거'));
    kids.push(rawBox(raw));
    setDetail({ illu: 'pin-store', title: '이 조건으로 골라 받아요', sub: '센터 화면에서 지역과 업종을 이렇게 고르세요.', body: kids });
  }

  /* ---------- 상세: 공개 데이터로 본 것 ---------- */

  var GAP_ICON = { dong: 'pin', region: 'pin', slot: 'clock', dow: 'calendar', dow_x_slot: 'calendar', age: 'users', who: 'users', user_type: 'users', sex: 'users', nationality: 'users', biz: 'store' };

  function renderDetailPublic() {
    var data = state.run, need = data.need || {}, pa = data.public_answer || {};
    var picked = pickedMonths(data.order);
    var kids = [];
    var subj = txt(need.biz) || txt(need.subject_label);
    var sub = pa.available === false ? '이 질문은 공개 데이터로는 볼 수 없어요.' : (subj ? subj + ', 제주도 전체 값이에요.' : '제주도 전체 값이에요.');
    if (pa.available === false) {
      if (pa.reason) kids.push(el('div', { class: 'mini-band' }, icon('info'), el('div', { text: easy(pa.reason) })));
      var ref = pa.reference;
      if (ref && typeof ref === 'object' && arr(ref.seasonal).length) kids.push(seasonChart(ref.seasonal, picked, '참고', easy(ref.title)));
    } else {
      var sc = seasonChart(pa.seasonal, picked, null, '');
      if (sc) kids.push(sc);
      var line = lineChart(pa.series, pa.metric);
      if (line) kids.push(line);
      arr(pa.shares).map(shareTable).filter(Boolean).forEach(function (t) { kids.push(t); });
    }
    var gaps = list(data.gaps);
    var fill = gaps.filter(function (g) { return txt(g.status).indexOf('없') < 0; });
    var none = gaps.filter(function (g) { return txt(g.status).indexOf('없') >= 0; });
    var inPub = arr(need.units).filter(function (u) { return u && u.in_public; }).map(function (u) { return txt(u.label || u.key); });
    var gapRows = [];
    var done = {};
    arr(state.csv).forEach(function (c) { if (c && c.r) fill.forEach(function (g) { if (gapFilledBy(g.key, c.r)) done[g.key] = true; }); });
    var doneList = fill.filter(function (g) { return done[g.key]; });
    fill = fill.filter(function (g) { return !done[g.key]; });
    if (doneList.length) gapRows.push(kvRow('받은 파일로 채운 것', el('span', { class: 'tags' }, doneList.map(function (g) { return el('span', { class: 'tag ok' }, icon('check-circle'), txt(g.label || g.key)); }))));
    if (fill.length) gapRows.push(kvRow('센터에서 받아 채우는 것', el('span', { class: 'tags' }, fill.map(function (g) { return el('span', { class: 'tag fill' }, icon(GAP_ICON[g.key] || 'layers'), txt(g.label || g.key)); }))));
    if (none.length) gapRows.push(kvRow('센터에도 없는 것', el('span', { class: 'tags' }, none.map(function (g) { return el('span', { class: 'tag' }, icon('none-circle'), txt(g.label || g.key)); }))));
    if (inPub.length) gapRows.push(kvRow('공개 데이터로 볼 수 있는 것', inPub.join(' · ')));
    if (gapRows.length) kids.push(el('h2', { class: 'h2', text: '공개 데이터에 없는 것' }), el('ul', { class: 'slip read' }, gapRows));
    if (!kids.length) kids.push(el('p', { class: 'body-text muted', text: '공개 데이터로 계산한 값이 없어요.' }));
    kids.push(rawBox([rawLine('계산에 쓴 공개 데이터', pa.source), rawLine('까닭', pa.reason), rawList(pa.notes, '메모'),
      rawList(gaps.map(function (g) { return txt(g.label || g.key) + ': ' + txt(g.status) + (arr(g.by).length ? ' (' + arr(g.by).join(', ') + ')' : '') + (g.note ? ' - ' + txt(g.note) : ''); }), '빈칸')]));
    setDetail({ illu: 'line-doc', title: '공개 데이터로 먼저 봤어요', sub: sub, body: kids });
  }

  /* ---------- 상세: 센터에 없는 정보 · 비슷한 자료 · 두 방법 ---------- */

  function renderDetailNoData() {
    var nod = list(state.run.order && state.run.order.no_data);
    var kids = [], raw = [];
    nod.forEach(function (n) {
      var inst = arr(n.instead).map(txt).filter(Boolean);
      if (nod.length > 1) kids.push(el('h2', { class: 'h2', text: josa(txt(n.unit) || '이 정보', '은는') + ' 센터에도 없어요' }));
      if (nod.length > 1 && n.explain) kids.push(el('p', { class: 'body-text', text: easy(n.explain) }));
      if (inst.length) {
        kids.push(el('h2', { class: 'h2', text: '대신 볼 수 있는 것' }));
        kids.push(el('ol', { class: 'num-list' }, inst.map(function (s) { return el('li', { text: easy(dropLead(s)) }); })));
      }
      raw.push(el('h3', { text: txt(n.unit) + (n.key ? ' (' + txt(n.key) + ')' : '') }), rawLine('왜 없나요', n.explain), rawList(inst, '대신'));
    });
    kids.push(rawBox(raw));
    var first = nod[0] || {};
    setDetail({ illu: 'none', title: '이 정보는 센터에 없어요', sub: nod.length === 1 && first.explain ? easy(first.explain) : '', body: kids });
  }

  function renderDetailOos() {
    var oos = (state.run.order || {}).out_of_scope || {};
    var ds = list(oos.datasets);
    var kids = [el('p', { class: 'body-text', text: easy(oos.message) }),
      el('ul', { class: 'slip read' }, ds.map(function (d) {
        var per = txt(d.period_text);
        return slipRow('', dsName(d.dataset_name), null, { note: el('span', { class: 'slip-label', text: (per.indexOf('수록') === 0 ? '' : '수록 ') + ymInText(per) + ' · 반출 ' + txt(d.export) }) });
      }))];
    if (state.run.mode_used !== 'llm') kids.push(el('p', { class: 'small-note', text: 'AI 없이 판단했어요.' }));
    kids.push(rawBox([rawLine('주제', oos.topic), rawLine('읽은 낱말', oos.word), rawLine('안내 원문', oos.message),
      rawList(ds.map(function (d) { return txt(d.dataset_no) + ' ' + txt(d.dataset_name) + ' (' + txt(d.period_text) + ', 반출 ' + txt(d.export) + ')'; }), '자료')]));
    setDetail({ illu: 'oos', title: '센터에 있는 비슷한 자료', sub: '목록은 못 만들지만 이런 이름의 자료가 있어요.', body: kids });
  }

  function renderAlts() {
    var order = state.run.order || {}, alts = arr(order.alternatives).slice();
    alts.sort(function (a, b) { return (b && b.recommended ? 1 : 0) - (a && a.recommended ? 1 : 0); });
    var kids = alts.map(function (a) {
      a = a || {};
      var its = arr(a.items);
      var rename = nameByNo(its);
      var listOf = function (v) {
        var l = arr(v).map(txt).filter(Boolean).map(rename).map(easy);
        return l.length ? el('ul', { class: 'dot-list' }, l.map(function (s) { return el('li', { text: s }); })) : el('p', { class: 'muted', text: '없어요' });
      };
      return el('section', { class: 'alt-card' + (a.recommended ? ' rec' : ''), 'aria-label': plain(altTitle(a.title)) + (a.recommended ? ' (추천 방법)' : '') },
        a.recommended ? el('span', { class: 'rec-badge' }, icon('check'), '추천 방법') : null,
        el('h2', { text: plain(altTitle(a.title)) }),
        el('p', { class: 'alt-dl' }, el('b', { text: fmtCount(a.downloads, '번') }), ' 받기 · ', its.map(function (it) { return dsName(it && it.dataset_name ? it.dataset_name : it); }).join(', ')),
        el('p', { class: 'alt-k', text: '이 방법으로 볼 수 있는 것' }), listOf(a.gain),
        el('p', { class: 'alt-k', text: '이 방법으로는 못 보는 것' }), listOf(a.give_up));
    });
    kids.push(rawBox(rawList(alts.map(function (a) {
      return txt(a.title) + ' - ' + arr(a.items).map(function (x) { return txt(x && x.dataset_no) + ' ' + txt(x && x.dataset_name); }).join(', ') +
        (a.downloads_calc ? ' / ' + txt(a.downloads_calc) : '') + (a.recommended ? ' (먼저 권함)' : '');
    }))));
    setDetail({ illu: 'fork-two', title: '두 가지 방법이 있어요', sub: '추천 방법을 먼저 보여 드려요.', body: kids });
  }

  /* ---------- 상세: 믿어도 될까요 ---------- */

  function renderTrust() {
    var data = state.run, v = data.validation || {}, trace = arr(data.trace), s = data.sent_to_llm || {};
    var isLlm = data.mode_used === 'llm';
    var has = isNum(v.passed) && isNum(v.total) && v.total > 0;
    var unv = isNum(v.unverified) ? v.unverified : 0;
    var allOk = has && v.passed === v.total && unv === 0;
    var title = !has ? '명세서와 맞춰 본 기록이에요' : (allOk ? '명세서와 하나하나 맞춰 봤어요' : '확인할 것이 있어요');
    /* 설명 줄도 제목과 같은 셈으로(확인할 것이 남으면 "모두 맞아요" 라고 하지 않는다. validationBox 와 같은 말) */
    var sub = !has ? '' : (allOk ? v.total + '개 항목이 모두 맞아요.'
      : (v.passed === v.total ? v.total + '개 가운데 ' + (v.passed - unv) + '개는 같고, ' + unv + '개는 센터 화면에서 확인해 주세요.'
        : v.total + '개 중 ' + v.passed + '개가 맞아요.'));
    var mainCount = trace.filter(function (t) { return !(t && t.sub); }).length;
    var cv = isNum(s.center_values) ? s.center_values : 0;
    var exAny = list((data.order || {}).items).length > 0;
    var lim = arr(data.limits).filter(Boolean).length;
    var rows = [
      slipRow('명세서와 맞춰 본 것', has ? v.passed + '/' + v.total + ' 맞음' + (unv ? ' · 확인할 것 ' + unv + '개' : '') : '기록 없음', '/trust/check',
        { lead: has && allOk ? 'check-circle' : 'alert', leadCls: has && allOk ? 'ok' : 'warn' }),
      slipRow(isLlm ? 'AI가 한 일' : '프로그램이 한 일', mainCount + '단계', '/trust/ai', { lead: 'layers' }),
      slipRow('AI로 보낸 내용', '센터 값 ' + fmtNum(cv) + '건', '/trust/sent', { lead: 'lock' })];
    if (exAny) rows.push(slipRow('가져가기(반출) 규칙', '센터 밖으로 가져가는 법', '/trust/export', { lead: 'upload' }));
    if (lim) rows.push(slipRow('알아 둘 점', lim + '가지', '/trust/limits', { lead: 'info' }));
    setDetail({ illu: 'stamp-check', title: title, sub: sub, back: '/list', body: [
      el('ul', { class: 'slip' }, rows),
      el('p', { class: 'small-note', text: '화면의 숫자는 모두 프로그램이 계산한 값이에요.' })] });
  }

  function validationBox(data) {
    var v = data.validation || {};
    if (!(isNum(v.passed) && isNum(v.total) && v.total > 0)) return el('p', { class: 'body-text muted', text: '이번 결과에는 맞춰 본 기록이 없어요.' });
    var unv = isNum(v.unverified) ? v.unverified : 0;
    var bad = v.total - v.passed, same = v.passed - unv;
    if (bad === 0 && unv === 0) {
      return el('div', { class: 'info-box ok' }, icon('check-circle'), el('div', { class: 'info-box-text' },
        el('strong', { text: '센터 데이터 설명서(명세서)와 ' + v.total + '개 항목을 모두 맞춰 봤어요.' }),
        el('span', { text: '받을 자료 이름, 달, 조건 값, 받을 항목, 받는 횟수가 설명서와 같아요.' })));
    }
    var inner = el('div', { class: 'info-box-text' });
    if (bad > 0) {
      inner.appendChild(el('strong', { text: bad + '개가 설명서와 맞지 않아 목록에서 뺐어요.' }));
      var issues = arr(v.issues).map(txt).filter(Boolean).map(easy);
      if (issues.length) inner.appendChild(el('ul', { class: 'dot-list' }, issues.map(function (s) { return el('li', { text: s }); })));
    }
    if (unv > 0) {
      inner.appendChild(el('strong', { text: v.total + '개 가운데 ' + same + '개는 설명서와 같아요. ' + unv + '개는 센터 화면에서 확인해 주세요.' }));
      var lines = unverifiedConds(list(data.order && data.order.items)).map(function (c) {
        return condName(c).k + ' ' + condValue(c) + ': 설명서만으로는 이 자료의 표기를 확인하지 못했어요. 내려받을 때 센터 화면에서 확인하세요.';
      });
      if (lines.length) inner.appendChild(el('ul', { class: 'dot-list' }, lines.map(function (s) { return el('li', { text: s }); })));
      if (list(data.order && data.order.alternatives).length && lines.length < unv) {
        inner.appendChild(el('span', { class: 'muted', text: '같은 줄이 견준 방법에도 들어 있으면 따로 세어서 확인할 것의 수가 더 많을 수 있어요.' }));
      }
    }
    return el('div', { class: 'info-box ' + (bad > 0 ? 'bad' : 'warn') }, icon(bad > 0 ? 'x-circle' : 'alert'), inner);
  }

  function evidenceRaw(data) {
    var order = data.order || {}, items = list(order.items), alts = arr(order.alternatives), v = data.validation || {};
    var out = [];
    items.forEach(function (it) {
      out.push(el('h3', { text: '명세서 ' + txt(it.dataset_no) + ' ' + txt(it.dataset_name) }));
      out.push(rawList(arr(it.evidence).map(function (e) { return typeof e === 'string' ? e : txt(e && (e.note || e.text)); })));
      list(it.conditions).forEach(function (c) {
        var ev = c.evidence || {};
        out.push(el('p', { text: txt(c.column) + ' = ' + txt(c.value) + (c.value_label ? ' (' + txt(c.value_label) + ')' : '') + ': ' + txt(ev.note) + (ev.verified === false ? ' [명세서로 확인 못 함]' : '') }));
      });
    });
    if (alts.length) out.push(rawList(alts.map(function (a) { return txt(a.title) + ' - ' + arr(a.items).map(function (x) { return txt(x && x.dataset_no); }).join(', '); }), '견준 방법'));
    if (isNum(v.total)) out.push(rawLine('대조', '통과 ' + v.passed + '/' + v.total + (v.unverified ? ', 확인 필요 ' + v.unverified : '') + (v.breakdown_text ? ' (센 줄: ' + txt(v.breakdown_text) + ')' : '')));
    out.push(rawList(v.unverified_notes, '확인하지 못한 줄'));
    out.push(rawList(v.issues, '어긋난 줄'));
    var spec = data.spec_label || (state.status && state.status.spec_label) || '';
    out.push(rawLine('기준 명세서', spec));
    return out;
  }

  var TOOL_KO = {
    public_scope: '공개 데이터 범위 살펴보기',
    public_monthly: '공개 데이터로 달마다 계산하기',
    catalog_search: '명세서에서 자료 찾기',
    pick_months: '받을 달 고르기',
    submit_order: '목록을 내고 명세서와 맞춰 보기',
    '해석': '문장 읽기'
  };

  function runModeBox(data) {
    if (data.mode_used === 'llm') return null;
    var fb = txt(data.fallback_reason);
    var inner = el('div', { class: 'info-box-text' },
      el('strong', { text: fb && state.inputKind === 'text' ? 'AI를 쓰지 못해 AI 없이 만들었어요.' : 'AI 없이 만들었어요.' }),
      el('span', { text: '숫자 계산, 받을 달 고르기, 설명서 대조는 AI 없이도 똑같이 해요. 문장은 미리 정한 낱말로 읽어요.' }));
    if (fb) {
      var cut = fb.indexOf(' 조치: ');
      var modeWords = function (t) {
        return t.replace(/ANTHROPIC_API_KEY\s*/g, 'AI 열쇠(Key)').replace(/Key\s+(를|가)/g, 'Key$1')
          .replace(/규칙 모드로/g, 'AI 없이').replace(/규칙 모드/g, 'AI 없이 실행').replace(/AI 모드가/g, 'AI를 쓰게').replace(/AI 모드/g, 'AI 사용');
      };
      var cause = modeWords(easy(cut >= 0 ? fb.slice(0, cut) : fb));
      var todo = cut >= 0 ? modeWords(easy(fb.slice(cut + 5).trim())) : '';
      var dl = el('dl', { class: 'cause' }, el('dt', { text: '원인' }), el('dd', { text: cause }));
      if (todo) append(dl, [el('dt', { text: '할 일' }), el('dd', { text: todo })]);
      inner.appendChild(dl);
    }
    return el('div', { class: 'info-box' }, icon('info'), inner);
  }

  function renderTrustPage(kind) {
    var data = state.run;
    var kids = [], o = { back: '/detail/trust' };
    if (kind === 'check') {
      o.illu = 'stamp-check'; o.title = '명세서와 맞춰 본 것';
      o.sub = '센터가 데이터마다 적어 둔 공식 설명서(명세서)와 견줬어요.';
      kids.push(validationBox(data), rawBox(evidenceRaw(data)));
    } else if (kind === 'ai') {
      var isLlm = data.mode_used === 'llm', trace = arr(data.trace);
      o.illu = 'doc-search';
      o.title = isLlm ? 'AI가 한 일' : '프로그램이 한 일';
      o.sub = isLlm ? 'AI가 도구를 불러 차례로 한 일이에요.' : '프로그램이 차례로 한 일이에요.';
      var rm = runModeBox(data);
      if (rm) kids.push(rm);
      var n = 0;
      var steps = trace.map(function (t) {
        t = t || {};
        var tool = txt(t.tool);
        var rawK = rawBox([rawLine('도구', tool), rawLine('넣은 값', t.args_summary), rawLine('결과', t.result_summary), rawLine('덧붙임', t.note)],
          '원문: ' + (t.sub ? (txt(t.result_summary) === '버림' ? '고르지 않은 후보' : (TOOL_KO[tool] || '딸린 일')) : (TOOL_KO[tool] || plain(tool) || '판단')));
        if (t.sub) return el('li', { class: 'sub' }, el('span', { text: txt(t.result_summary) === '버림' ? '고르지 않은 후보' : (TOOL_KO[tool] || '딸린 일') }), rawK);
        n += 1;
        return el('li', null, el('div', { class: 'trace-head' }, el('span', { class: 'trace-no', text: String(n) }),
          el('span', { class: 'trace-name', text: TOOL_KO[tool] || plain(tool) || '판단' })), rawK);
      });
      kids.push(steps.length ? el('ol', { class: 'trace' }, steps) : el('p', { class: 'body-text muted', text: '남은 기록이 없어요.' }));
      var llm = data.llm;
      kids.push(rawBox([rawLine('실행 방식', data.mode_used), rawLine('AI를 쓰지 못한 까닭', data.fallback_reason),
        isLlm && llm && typeof llm === 'object' ? rawLine('쓴 AI', txt(llm.model) + ' · 주고받은 횟수 ' + fmtCount(llm.turns) + '번 · 보낸 글 ' + fmtCount(llm.input_tokens) + '토큰 · 받은 글 ' + fmtCount(llm.output_tokens) + '토큰') : null]));
    } else if (kind === 'sent') {
      var s = data.sent_to_llm || {};
      var isRule = data.mode_used !== 'llm';
      var cv = isNum(s.center_values) ? s.center_values : 0;
      o.illu = 'stamp-check'; o.title = 'AI로 보낸 내용';
      o.sub = '센터에서 받은 파일의 값은 AI로 보내지 않아요.';
      kids.push(el('ul', { class: 'slip read' },
        kvRow('내가 적은 문장', sentValue(s.purpose_text, '건')),
        kvRow('공개 데이터 요약값', sentValue(s.public_aggregates, '건')),
        kvRow('명세서 항목', sentValue(s.spec_items, '건')),
        kvRow('센터 파일 값', el('span', null, el('b', { text: fmtNum(cv) + '건' }), ' · 다시 계산은 이 컴퓨터 안에서만 해요'))));
      if (isRule && (s.purpose_text || (isNum(s.llm_requests) && s.llm_requests > 0))) {
        kids.push(el('p', { class: 'body-text', text: 'AI를 ' + fmtCount(s.llm_requests || 0) + '번 부른 뒤(응답 ' + fmtCount(s.llm_calls || 0) + '번) AI 없이 계산으로 넘겼어요. 위는 그때까지 보낸 내용이에요.' }));
      } else if (isRule) {
        kids.push(el('p', { class: 'body-text muted', text: '이번에는 AI를 부르지 않았어요.' }));
      }
    } else if (kind === 'export') {
      var items = list((data.order || {}).items);
      o.illu = 'stamp-check'; o.title = '가져가기(반출) 규칙';
      o.sub = '센터 밖으로 가져가기는 정해진 규칙대로만 할 수 있어요.';
      var seen = {};
      items.forEach(function (it, i) {
        var ex = it.export || {};
        var key = JSON.stringify(ex);
        if (seen[key]) return;
        seen[key] = 1;
        if (items.length > 1) kids.push(el('h2', { class: 'h2', text: dsName(it.dataset_name) }));
        kids.push(el('div', { class: 'info-box' }, icon('lock'), el('div', { class: 'info-box-text', text: exportStatement(ex) })));
        var notes = arr(ex.notes).map(txt).filter(Boolean);
        if (notes.length) kids.push(el('ol', { class: 'num-list' }, notes.map(function (s) { return el('li', { text: easy(s) }); })));
      });
      kids.push(rawBox(items.map(function (it) { return rawList(arr((it.export || {}).notes), txt(it.dataset_no) + ' ' + txt(it.dataset_name) + ' (반출 ' + txt((it.export || {}).allowed) + ')'); })));
    } else if (kind === 'limits') {
      var lim = arr(data.limits).map(txt).filter(Boolean);
      o.illu = 'stamp-check'; o.title = '알아 둘 점';
      o.sub = '결과를 읽을 때 함께 봐 주세요.';
      kids.push(el('ol', { class: 'num-list' }, lim.map(function (s) { return el('li', { text: easy(s) }); })));
      kids.push(rawBox(rawList(lim)));
    }
    o.body = kids;
    setDetail(o);
  }

  function sentValue(v, unit) {
    if (v === true) return '보냄';
    if (v === false || v === null || v === undefined) return '보내지 않음';
    if (Array.isArray(v)) return v.length + unit;
    if (isNum(v)) return v === 0 ? '보내지 않음(0' + unit + ')' : fmtNum(v) + unit;
    return txt(v);
  }

  /* ---------- 도움말 · 프로그램 상태 ---------- */

  function renderStatusPage() {
    var s = state.status;
    if (!s) {
      setDetail({ illu: 'gauge', title: '확인할 것이 있어요', sub: '자가 점검 결과를 불러오지 못했어요.',
        body: [state.statusError ? el('div', { class: 'errorbox' }, el('div', { class: 'errorbox-in' }, icon('x-circle', 'err-ico'),
          el('dl', null, el('dt', { text: '원인' }), el('dd', { text: state.statusError.message }), state.statusError.action ? [el('dt', { text: '할 일' }), el('dd', { text: state.statusError.action })] : null))) : null] });
      return;
    }
    var f = statusFacts(s), llm = s.llm || {}, pub = s.public || {};
    var rows = [
      slipRow('자가 점검', f.okCount + '/' + f.checks.length + (f.failed.length ? ' · 확인할 것 ' + f.failed.length + '개' : ' 통과'), null,
        { lead: f.problem ? 'alert' : 'check-circle', leadCls: f.problem ? 'warn' : 'ok' }),
      slipRow('실행 방식', llm.enabled ? 'AI 사용' : 'AI 없이', null, { lead: 'info' }),
      slipRow('명세서', specSentence(s.spec), null, { lead: 'layers' }),
      slipRow('공개 데이터', el('span', null, publicSentence('카드', pub.d1048), el('br'), publicSentence('유동인구', pub.d520)), null, { lead: 'calendar' }),
      slipRow('센터 파일 예시', '명세서대로 만든 연습용 가상 파일이에요', null, { lead: 'upload' })];
    var checkList = el('ul', { class: 'slip read' }, f.checks.map(function (c) {
      return slipRow('', el('span', null, el('span', { text: easy(txt(c.name).replace(/허브\s*\d+\s*/, '')) }), el('span', { class: 'sr-only', text: c.ok ? ' 통과' : ' 확인 필요' })), null,
        { lead: c.ok ? 'check-circle' : 'x-circle', leadCls: c.ok ? 'ok' : 'bad', note: c.ok ? null : el('span', { class: 'slip-note', text: easy(c.detail) }) });
    }));
    var body = [el('ul', { class: 'slip' }, rows)];
    if (!llm.enabled && llm.reason) body.push(runModeBox({ mode_used: 'rule', fallback_reason: llm.reason }));
    body.push(el('h2', { class: 'h2', text: '점검한 것' }), checkList,
      rawBox([rawList(f.checks.map(function (c) { return (c.ok ? '[통과] ' : '[실패] ') + txt(c.name) + ' - ' + txt(c.detail); }), '자가 점검'),
        rawLine('AI 모델', llm.model), rawLine('AI 연결 확인', llm.checked), rawLine('AI 없이 실행하는 까닭', llm.reason),
        rawLine('기준 명세서', s.spec_label), rawLine('카드 공개 데이터 파일', pub.d1048 && pub.d1048.file), rawLine('유동인구 공개 데이터 파일', pub.d520 && pub.d520.file)]));
    setDetail({ illu: 'gauge', title: f.problem ? '확인할 것이 있어요' : '프로그램이 준비됐어요',
      sub: llm.enabled ? 'AI를 쓰고 있어요.' : 'AI 없이 실행 중이에요.', body: body });
  }

  /* ---------- 그래프: 공통 가리킴(마우스, 키보드) ---------- */

  function attachHover(svg, W, H, pts, onShow, onHide) {
    var tip = $('chartTip');
    var cur = -1;
    function place(i) {
      var p = pts[i], r = svg.getBoundingClientRect();
      clear(tip);
      tip.appendChild(el('strong', { text: p.value }));
      tip.appendChild(document.createTextNode(p.label));
      tip.hidden = false;
      var cx = r.left + p.x * r.width / W, cy = r.top + p.y * r.height / H;
      var tw = tip.offsetWidth, th = tip.offsetHeight;
      var left = cx + 12;
      if (left + tw > window.innerWidth - 6) left = cx - 12 - tw;
      var top = cy - th - 10;
      if (top < 4) top = cy + 14;
      tip.style.left = Math.max(4, left) + 'px';
      tip.style.top = top + 'px';
    }
    function show(i) {
      if (i < 0 || i >= pts.length) return;
      cur = i;
      onShow(i);
      place(i);
    }
    function hide() { tip.hidden = true; onHide(); }
    svg.addEventListener('pointermove', function (e) {
      var r = svg.getBoundingClientRect();
      var x = (e.clientX - r.left) * W / r.width;
      var best = 0, bd = Infinity;
      for (var i = 0; i < pts.length; i++) { var d = Math.abs(pts[i].x - x); if (d < bd) { bd = d; best = i; } }
      show(best);
    });
    svg.addEventListener('pointerleave', hide);
    svg.addEventListener('focus', function () { show(cur < 0 ? pts.length - 1 : cur); });
    svg.addEventListener('blur', hide);
    svg.addEventListener('keydown', function (e) {
      var i = cur < 0 ? pts.length - 1 : cur;
      if (e.key === 'ArrowLeft') i = Math.max(0, i - 1);
      else if (e.key === 'ArrowRight') i = Math.min(pts.length - 1, i + 1);
      else if (e.key === 'Home') i = 0;
      else if (e.key === 'End') i = pts.length - 1;
      else if (e.key === 'Escape') { hide(); return; }
      else return;
      e.preventDefault();
      show(i);
    });
  }

  /* 그림 폭은 상세 페이지 본문 폭에 맞춘다(최소 320, 최대 640). 페이지를 보인 뒤 그린다 */
  function chartWidth() {
    var w = $('detailBody').clientWidth || 600;
    return Math.max(320, Math.min(640, Math.round(w)));
  }
  function lowScreen() { return window.innerHeight <= 820 || window.innerWidth <= 1400; }

  function niceTicks(min, max, n) {
    if (!(max > min)) { var pad = Math.abs(min) > 0 ? Math.abs(min) * 0.1 : 1; min -= pad; max += pad; }
    var raw = (max - min) / n;
    var mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    var norm = raw / mag;
    var step = (norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 5 ? 5 : 10) * mag;
    var lo = Math.floor(min / step + 1e-9) * step, hi = Math.ceil(max / step - 1e-9) * step;
    var ticks = [];
    for (var k = 0; lo + k * step <= hi + step * 1e-6; k++) ticks.push(lo + k * step);
    var dec = Math.max(0, -Math.floor(Math.log(step) / Math.LN10 + 1e-9));
    return { lo: lo, hi: hi, ticks: ticks, dec: Math.min(dec, 4) };
  }

  function tableToggle(headers, rows, numericFrom) {
    var table = el('table', null, el('thead', null, el('tr', null, headers.map(function (h, i) {
      return el('th', { scope: 'col', class: i >= numericFrom ? 'num' : null, text: h });
    }))));
    var tb = el('tbody');
    rows.forEach(function (r) {
      tb.appendChild(el('tr', null, r.map(function (c, i) { return el('td', { class: i >= numericFrom ? 'num' : null, text: c }); })));
    });
    table.appendChild(tb);
    return el('details', { class: 'chart-table' }, el('summary', { text: '표로 보기' }), el('div', { class: 'table-wrap' }, table));
  }

  /* 월별 흐름(꺾은선) */
  function lineChart(series, metric) {
    var unit = metricUnit(metric);
    var data = [];
    arr(series).forEach(function (d, i) {
      if (!d || !isNum(d.value)) return;
      var p = parseYm(d.ym);
      data.push({ ord: p ? p.y * 12 + (p.m - 1) : null, ym: fmtYm(d.ym), p: p, v: d.value, i: i });
    });
    if (!data.length) return null;
    var byTime = data.every(function (d) { return d.ord !== null; });
    if (byTime) data.sort(function (a, b) { return a.ord - b.ord; });
    data.forEach(function (d, i) { d.pos = byTime ? d.ord : i; });

    var vmin = Math.min.apply(null, data.map(function (d) { return d.v; }));
    var vmax = Math.max.apply(null, data.map(function (d) { return d.v; }));
    var t = niceTicks(vmin, vmax, 4);
    /* 금액은 원 단위 11자리라 읽기 어렵다. 세로축 눈금만 억 원으로 줄인다(표와 가리킴 글자는 원 단위 그대로) */
    var inEok = unit === '원' && vmax >= 1e8;
    var tickText = t.ticks.map(function (v) {
      if (!inEok) return fmtNum(v, t.dec);
      var e = v / 1e8;
      return fmtNum(e, Number.isInteger(e) ? 0 : 1) + '억';
    });
    var longest = Math.max.apply(null, tickText.map(function (s) { return s.length; }));

    var W = chartWidth(), H = lowScreen() ? 200 : 220, mL = Math.max(44, Math.round(longest * 8.5) + 14), mR = 14, mT = 12, mB = 30;
    var x0 = data[0].pos, x1 = data[data.length - 1].pos;
    var fx = function (pos) { return x1 === x0 ? mL + (W - mL - mR) / 2 : mL + (pos - x0) * (W - mL - mR) / (x1 - x0); };
    var fy = function (v) { return mT + (t.hi - v) * (H - mT - mB) / (t.hi - t.lo); };
    var last = data[data.length - 1];

    var svg = sv('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img', tabindex: '0',
      'aria-label': '월별 흐름 꺾은선 그림. ' + fmtYmKo(data[0].ym) + '부터 ' + fmtYmKo(last.ym) + '까지 ' + data.length + '개 달. 좌우 화살표 키로 달을 옮겨 값을 볼 수 있어요. 같은 값이 아래 "표로 보기"에 있어요.' });

    t.ticks.forEach(function (v, i) {
      var y = fy(v);
      svg.appendChild(sv('line', { class: 'grid', x1: mL, x2: W - mR, y1: y, y2: y }));
      svg.appendChild(sv('text', { x: mL - 8, y: y + 5, 'text-anchor': 'end', text: tickText[i] }));
    });
    svg.appendChild(sv('line', { class: 'axis', x1: mL, x2: W - mR, y1: H - mB, y2: H - mB }));

    if (byTime) {
      var years = (x1 - x0) / 12;
      var everyYear = years > 5 ? 2 : 1;
      if (years >= 1.5) {
        data.forEach(function (d) {
          if (d.p.m !== 1 || (d.p.y % everyYear) !== 0) return;
          var x = fx(d.pos);
          svg.appendChild(sv('line', { class: 'axis', x1: x, x2: x, y1: H - mB, y2: H - mB + 4 }));
          svg.appendChild(sv('text', { x: x, y: H - mB + 19, 'text-anchor': 'middle', text: d.p.y + '년' }));
        });
      } else {
        var stepN = Math.max(1, Math.ceil(data.length / 6));
        data.forEach(function (d, i) {
          if (i % stepN) return;
          svg.appendChild(sv('text', { x: fx(d.pos), y: H - mB + 19, 'text-anchor': 'middle', text: d.ym }));
        });
      }
    }

    var path = data.map(function (d, i) { return (i ? 'L' : 'M') + fx(d.pos).toFixed(1) + ' ' + fy(d.v).toFixed(1); }).join(' ');
    svg.appendChild(sv('path', { class: 'line', d: path }));
    svg.appendChild(sv('circle', { class: 'dot', cx: fx(last.pos), cy: fy(last.v), r: 4 }));

    var hair = sv('line', { class: 'hair', y1: mT, y2: H - mB, visibility: 'hidden' });
    var dot = sv('circle', { class: 'dot', r: 5, visibility: 'hidden' });
    append(svg, [hair, dot]);
    svg.appendChild(sv('rect', { x: 0, y: 0, width: W, height: H, fill: 'transparent' }));

    var pts = data.map(function (d) { return { x: fx(d.pos), y: fy(d.v), value: fmtNum(d.v) + unit, label: fmtYmKo(d.ym) + ' ' + txt(metric) }; });
    attachHover(svg, W, H, pts, function (i) {
      hair.setAttribute('x1', pts[i].x); hair.setAttribute('x2', pts[i].x); hair.setAttribute('visibility', 'visible');
      dot.setAttribute('cx', pts[i].x); dot.setAttribute('cy', pts[i].y); dot.setAttribute('visibility', 'visible');
    }, function () { hair.setAttribute('visibility', 'hidden'); dot.setAttribute('visibility', 'hidden'); });

    var cap = (metric ? txt(metric) + (unit ? '(' + unit + (inEok ? ', 세로축은 억 원' : '') + ')' : '') + ' · ' : '') + fmtYmKo(data[0].ym) + '~' + fmtYmKo(last.ym) + ' · 제주도 전체';
    return el('div', { class: 'chart-block' },
      el('h2', { text: '달마다 어떻게 달라졌을까요?' }),
      el('p', { class: 'chart-cap', text: cap }),
      el('div', { class: 'chart' }, svg),
      tableToggle(['연월', txt(metric) || '값'], data.map(function (d) { return [fmtYmKo(d.ym), fmtNum(d.v) + unit]; }), 1));
  }

  /* 그림 아래 짧은 역할 글(서버 role 의 뜻 그대로) */
  function roleTag(role) {
    var r = stripRef(role);
    if (r.indexOf('많') >= 0) return ['많은 달'];
    if (r.indexOf('적') >= 0) return ['적은 달'];
    if (r.indexOf('평균') >= 0) return ['평균에', '가까운 달'];
    return [r.slice(0, 6)];
  }

  function pickedMonths(order) {
    var picked = {};
    var items = list(order && order.items);
    for (var i = 0; i < items.length; i++) {
      var ms = list(items[i] && items[i].months);
      if (!ms.length) continue;
      ms.forEach(function (m) { var p = parseYm(m.ym); if (p) picked[p.m] = txt(m.role); });
      break;
    }
    return picked;
  }

  /* 1~12월 계절 지수(막대). 평균선 하나와 "100" 라벨, 고른 달만 청록·값·역할 글(색만으로 구분하지 않음) */
  function seasonChart(seasonal, picked, refLabel, subline) {
    var data = arr(seasonal).filter(function (d) { return d && isNum(d.index) && isNum(Number(d.month)); })
      .map(function (d) { return { m: Number(d.month), v: d.index }; })
      .sort(function (a, b) { return a.m - b.m; });
    if (!data.length) return null;
    var vals = data.map(function (d) { return d.v; });
    var vmax = Math.max.apply(null, vals), vmin = Math.min.apply(null, vals);
    var base = vmax < 10 ? 1 : (vmax < 1000 ? 100 : vals.reduce(function (a, b) { return a + b; }, 0) / vals.length);
    var dec = base === 1 ? 2 : 1;
    var hasTag = data.some(function (d) { return picked[d.m]; });

    var W = chartWidth(), H = lowScreen() ? 250 : 290, mL = 44, mR = 6, mT = 26, mB = hasTag ? 66 : 34;
    var span = Math.max(vmax - base, base - vmin, 1e-6);
    /* 평균보다 낮은 고른 달은 값 글자가 막대 아래에 붙는다. 달 이름과 겹치지 않게 아래에 자리를 더 둔다 */
    var lowPicked = data.some(function (d) { return picked[d.m] && d.v < base; });
    var top = base + span * 1.08, bot = base - span * (lowPicked ? 1.32 : 1.08);
    var fy = function (v) { return mT + (top - v) * (H - mT - mB) / (top - bot); };
    var yb = fy(base);
    var band = (W - mL - mR) / 12, bw = Math.min(34, band * 0.62);

    var svg = sv('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img', tabindex: '0',
      'aria-label': (refLabel ? '참고 그림. ' : '') + '1월부터 12월까지 계절 지수 막대 그림. 가운데 선이 평균 ' + fmtNum(base, dec) + '이에요. 목록에 넣은 달은 청록 막대와 아래 글자로 표시했어요. 좌우 화살표 키로 달을 옮겨 값을 볼 수 있어요. 같은 값이 아래 "표로 보기"에 있어요.' });

    var bars = [], pts = [];
    data.forEach(function (d) {
      var cx = mL + band * (d.m - 0.5), x = cx - bw / 2, y = fy(d.v);
      var up = d.v >= base, h = Math.abs(yb - y), r = Math.min(3, h, bw / 2);
      var role = picked[d.m];
      var dpath;
      if (h < 1) dpath = 'M' + x + ' ' + (yb - 1) + 'h' + bw + 'v2h' + (-bw) + 'z';
      else if (up) dpath = 'M' + x + ' ' + yb + 'V' + (y + r) + 'Q' + x + ' ' + y + ' ' + (x + r) + ' ' + y + 'H' + (x + bw - r) + 'Q' + (x + bw) + ' ' + y + ' ' + (x + bw) + ' ' + (y + r) + 'V' + yb + 'Z';
      else dpath = 'M' + x + ' ' + yb + 'V' + (y - r) + 'Q' + x + ' ' + y + ' ' + (x + r) + ' ' + y + 'H' + (x + bw - r) + 'Q' + (x + bw) + ' ' + y + ' ' + (x + bw) + ' ' + (y - r) + 'V' + yb + 'Z';
      var bar = sv('path', { class: 'bar' + (role ? ' picked' : ''), d: dpath });
      svg.appendChild(bar);
      bars.push(bar);
      if (role) svg.appendChild(sv('text', { class: 'val picked', x: cx, y: up ? y - 7 : y + 18, 'text-anchor': 'middle', text: fmtNum(d.v, dec) }));
      svg.appendChild(sv('text', { class: 'mlabel' + (role ? ' picked' : ''), x: cx, y: H - mB + 22, 'text-anchor': 'middle', text: d.m + '월' }));
      if (role) {
        roleTag(role).forEach(function (line, k) {
          svg.appendChild(sv('text', { class: 'tag', x: cx, y: H - mB + 40 + k * 16, 'text-anchor': 'middle', text: line }));
        });
      }
      pts.push({ x: cx, y: Math.min(y, yb), value: fmtNum(d.v, dec), label: d.m + '월 계절 지수' + (role ? ' - 목록에 넣은 달(' + stripRef(role) + ')' : '') });
    });
    svg.appendChild(sv('line', { class: 'base', x1: mL, x2: W - mR, y1: yb, y2: yb }));
    svg.appendChild(sv('text', { class: 'base-label', x: mL - 6, y: yb + 5, 'text-anchor': 'end', text: fmtNum(base, 0) }));
    svg.appendChild(sv('rect', { x: 0, y: 0, width: W, height: H, fill: 'transparent' }));

    attachHover(svg, W, H, pts, function (i) {
      bars.forEach(function (b, k) { b.classList.toggle('on', k === i); });
    }, function () { bars.forEach(function (b) { b.classList.remove('on'); }); });

    return el('div', { class: 'chart-block' },
      refLabel ? el('p', { class: 'chart-cap', text: '참고 그림이에요. ' + (subline || '') }) : null,
      el('div', { class: 'chart' }, svg),
      el('p', { class: 'legend-line' },
        el('span', null, el('span', { class: 'legend-sw on', 'aria-hidden': 'true' }), '목록에 넣은 달'),
        el('span', null, el('span', { class: 'legend-sw off', 'aria-hidden': 'true' }), '다른 달'),
        el('span', { text: '가운데 선 = 한 해 평균(' + fmtNum(base, dec) + ')' })),
      !refLabel && subline ? el('p', { class: 'chart-cap', text: subline }) : null,
      tableToggle(['달', '계절 지수', '목록에 넣은 달'], data.map(function (d) { return [d.m + '월', fmtNum(d.v, dec), picked[d.m] ? stripRef(picked[d.m]) : '']; }), 1));
  }

  function shareTable(s) {
    var rows = arr(s && s.rows).filter(Boolean);
    if (!rows.length) return null;
    var nums = rows.map(function (r) { return r.pct; }).filter(isNum);
    /* 서버가 0~1 로 준 경우에만 100 을 곱한다(합이 1 인 표). 계약은 퍼센트 값이다. */
    var sum = nums.reduce(function (a, b) { return a + b; }, 0);
    var scale = (nums.length >= 2 && Math.max.apply(null, nums) <= 1 && sum > 0.99 && sum < 1.01) ? 100 : 1;
    var max = nums.length ? Math.max.apply(null, nums) * scale : 0;
    /* 합이 100% 를 넘지 않는 비중이면 0~100% 그대로 그린다(가장 큰 값이 꽉 차 "전부"처럼 보이지 않게) */
    var whole = sum * scale <= 101 ? 100 : max;
    var tb = el('tbody');
    rows.forEach(function (r) {
      var p = isNum(r.pct) ? r.pct * scale : r.pct;
      tb.appendChild(el('tr', null,
        el('th', { scope: 'row' }, txt(r.label), isNum(p) && whole > 0 ? el('span', { class: 'share-bar', style: 'width:' + Math.max(0, Math.min(100, p / whole * 100)).toFixed(1) + '%', 'aria-hidden': 'true' }) : null),
        el('td', { class: 'num' + (isNum(p) ? '' : ' masked'), text: fmtPct(p) })));
    });
    return el('div', { class: 'table-wrap' }, el('table', { class: 'share-table' }, el('caption', { text: ymInText(plain(s.title)) }),
      el('thead', null, el('tr', null, el('th', { scope: 'col', text: '구분' }), el('th', { scope: 'col', class: 'num', text: '비중' }))), tb));
  }

  /* ---------- 인쇄용 주문서 (목업 6: 센터에 이 종이를 보여 주세요) ---------- */

  function psRow(k, v, cls) {
    return el('tr', { class: cls || null }, el('th', { scope: 'row', class: 'ps-k', text: k }), el('td', { class: 'ps-v' }, v));
  }

  /* 인쇄본의 반출 규칙은 첫 줄과 "근거" 줄만 적는다(나머지는 화면의 반출 규칙 페이지에 모두 있다) */
  function shortExport(ex) {
    var notes = arr(ex && ex.notes).map(txt).filter(Boolean);
    if (!notes.length) return null;
    var keep = [notes[0]];
    notes.slice(1).forEach(function (s) { if (/^근거/.test(s)) keep.push(s); });
    var rest = notes.length - keep.length;
    return el('ul', { class: 'ps-list ps-small' }, keep.map(function (s) { return el('li', { text: s }); }),
      rest > 0 ? el('li', { text: '그 밖의 반출 규칙 ' + rest + '가지는 화면의 "가져가기(반출) 규칙"에 있어요.' }) : null);
  }

  function renderPrintSheet(data) {
    var sheet = clear($('orderPanel'));
    sheet.appendChild(el('header', { class: 'ps-head' },
      el('p', { class: 'ps-app', text: '센터 방문 주문서' }),
      el('dl', { class: 'ps-meta' }, el('dt', { text: '만든 때' }), el('dd', { text: nowText() }),
        el('dt', { text: '만든 방식' }), el('dd', { text: data && data.mode_used === 'llm' ? 'AI 사용' : 'AI 없이' }))));
    var hero = function (t) { sheet.appendChild(el('div', { class: 'ps-hero' }, illu('ask'), el('h2', { class: 'ps-title', text: t }))); };
    if (!data) {
      hero('아직 받을 목록이 없어요');
      sheet.appendChild(el('p', { class: 'ps-msg', text: '첫 화면에서 궁금한 것을 적고 [받을 목록 만들기]를 누르세요.' }));
      return;
    }
    var order = data.order || {}, items = list(order.items), alts = list(order.alternatives), nod = list(order.no_data), v = data.validation || {};
    var tbl = el('table', { class: 'ps-table' });
    var tb = el('tbody');

    if (order.out_of_scope) {
      var oos = order.out_of_scope;
      hero('이 질문은 주문서를 만들지 않아요');
      tb.appendChild(psRow('궁금한 것', el('span', { text: state.purpose })));
      tb.appendChild(psRow('안내', [el('p', { text: txt(oos.message) }),
        el('ul', { class: 'ps-list' }, list(oos.datasets).map(function (d) { return el('li', { text: txt(d.dataset_name) + ' (' + txt(d.dataset_no) + ', ' + txt(d.period_text) + ', 반출 ' + txt(d.export) + ')' }); }))]));
      tbl.appendChild(tb);
      sheet.appendChild(tbl);
      return;
    }
    if (!items.length) {
      hero('센터에서 받을 것이 없어요');
      tb.appendChild(psRow('궁금한 것', el('span', { text: state.purpose })));
      tb.appendChild(psRow('안내', el('p', { text: txt(order.note) || '설명서와 맞춰 본 항목이 없어요.' })));
      tbl.appendChild(tb);
      sheet.appendChild(tbl);
    } else {
      hero('센터에 이 종이를 보여 주세요');
      items.forEach(function (it, i) {
        it = it || {};
        var grp = el('tbody', { class: 'ps-item' });
        tbl.appendChild(grp);
        var no = items.length > 1 ? '주문 ' + (i + 1) + ' · ' : '';
        var sub = ['명세서 번호 ' + txt(it.dataset_no)];
        if (it.provider) sub.push(txt(it.provider));
        sub.push('수록 ' + (it.period_text ? txt(it.period_text) : '명세서에 없음'));
        grp.appendChild(psRow(no + '받을 자료', [el('p', { class: 'ps-ds', text: txt(it.dataset_name) }), el('p', { class: 'ps-small', text: sub.join(' · ') })]));
        var months = list(it.months);
        if (months.length) {
          var mt = el('table', { class: 'ps-months' }, el('tbody', null, months.map(function (m) {
            var w = indexWords(m.index);
            return el('tr', null, el('td', { class: 'ps-ym' }, el('span', { class: 'ps-sq', 'aria-hidden': 'true' }), fmtYmKo(m.ym)),
              el('td', null, txt(m.role), w ? el('span', { class: 'ps-small', text: ' · ' + w }) : null));
          })));
          var mnote = arr(it.month_notes).map(txt).filter(Boolean)[0] || '';
          grp.appendChild(psRow('받을 달', [mt, mnote ? el('p', { class: 'ps-small', text: mnote }) : null]));
        }
        var conds = list(it.conditions);
        grp.appendChild(psRow('고를 조건', conds.length ? el('ul', { class: 'ps-conds' }, conds.map(function (c) {
          var ev = c.evidence || {};
          return el('li', null, el('span', { class: 'ps-ck', text: condName(c).k }), ' ', el('b', { text: condValue(c) }),
            el('span', { class: 'ps-code', text: '  (' + (txt(c.column_ko) || '컬럼') + ' ' + txt(c.column) + (c.value_label ? ' = ' + txt(c.value) : '') + ')' }),
            ev.verified === false ? el('span', { class: 'ps-warn', text: '  센터 화면에서 표기 확인' }) : null);
        })) : el('span', { text: '고를 조건 없음(전체를 받아요)' })));
        var cols = arr(it.use_columns);
        if (cols.length) grp.appendChild(psRow('받을 항목', el('span', null, cols.map(function (c, k) {
          return [k ? ' · ' : '', (c && typeof c === 'object') ? txt(c.ko) : txt(c), (c && typeof c === 'object' && c.en) ? el('span', { class: 'ps-code', text: '(' + txt(c.en) + ')' }) : null];
        }))));
        if (i === items.length - 1 || JSON.stringify(it.export) !== JSON.stringify(items[items.length - 1].export)) {
          var ex = it.export || {};
          grp.appendChild(psRow('가져가기(반출)', [el('p', { text: exportStatement(ex) }), shortExport(ex)]));
        }
      });
      tb.appendChild(psRow('궁금한 것', el('span', { text: state.purpose })));
      tbl.appendChild(tb);
      sheet.appendChild(tbl);
    }

    if (alts.length) {
      alts = alts.slice().sort(function (a, b) { return (b && b.recommended ? 1 : 0) - (a && a.recommended ? 1 : 0); });
      var at = el('table', { class: 'ps-alts' },
        el('caption', { text: '한 자료로는 다 볼 수 없어 견준 두 가지 방법' }),
        el('thead', null, el('tr', null, ['방법', '받을 자료', '받는 횟수', '볼 수 있는 것', '못 보는 것'].map(function (h) { return el('th', { scope: 'col', text: h }); }))));
      var atb = el('tbody');
      alts.forEach(function (a) {
        var rename = nameByNo(a.items);
        atb.appendChild(el('tr', null,
          el('td', null, el('b', { text: altTitle(a.title) }), a.recommended ? el('span', { class: 'ps-rec', text: ' 추천 방법' }) : null),
          el('td', { text: arr(a.items).map(function (x) { return txt(x && x.dataset_name) + '(' + txt(x && x.dataset_no) + ')'; }).join(', ') }),
          el('td', { text: fmtCount(a.downloads, '번') }),
          el('td', { text: arr(a.gain).map(txt).map(rename).join(' / ') }),
          el('td', { text: arr(a.give_up).map(txt).map(rename).join(' / ') })));
      });
      at.appendChild(atb);
      sheet.appendChild(at);
    }
    if (nod.length) {
      sheet.appendChild(el('section', { class: 'ps-sec' }, el('h3', { text: '센터에도 없는 정보' }),
        el('ul', { class: 'ps-list' }, nod.map(function (n) {
          return el('li', null, el('b', { text: txt(n.unit) + ': ' }), txt(n.explain),
            arr(n.instead).length ? el('span', { class: 'ps-small', text: ' 대신 할 수 있는 것: ' + arr(n.instead).map(txt).join(' / ') }) : null);
        }))));
    }
    var unv = isNum(v.unverified) ? v.unverified : 0;
    var bad = isNum(v.total) && isNum(v.passed) ? v.total - v.passed : 0;
    if (bad > 0 || unv > 0) {
      var lines = arr(v.issues).map(txt).filter(Boolean).map(function (s) { return '어긋나 뺀 줄: ' + s; })
        .concat(arr(v.unverified_notes).map(txt).filter(Boolean).map(function (s) { return '센터 화면에서 확인할 줄: ' + s; }));
      sheet.appendChild(el('section', { class: 'ps-sec' }, el('h3', { text: '센터 화면에서 확인할 것' }),
        el('ul', { class: 'ps-list' }, lines.map(function (s) { return el('li', { text: s }); }))));
    }
    if (items.length) {
      sheet.appendChild(el('p', { class: 'ps-info' }, el('span', { class: 'ps-info-mark', 'aria-hidden': 'true', text: 'i' }),
        el('span', { text: '센터 데이터는 한 번에 한 달 치씩만 받을 수 있어요. 모두 ' + fmtCount(totalDownloads(order, items), '번') + ' 받아요. 받은 달은 네모 칸에 표시해 주세요.' })));
    }
    /* 알아 둘 점은 앞 4줄만 싣고 나머지는 화면의 "알아 둘 점"으로 보낸다 */
    var limits = arr(data.limits).map(plain).filter(Boolean);
    if (limits.length) {
      var shownL = limits.slice(0, 4), restL = limits.length - shownL.length;
      sheet.appendChild(el('section', { class: 'ps-sec ps-limits' }, el('h3', { text: '알아 둘 점' }),
        el('ul', { class: 'ps-list' }, shownL.map(function (s) { return el('li', { text: s }); }),
          restL > 0 ? el('li', { text: '그 밖 ' + restL + "가지는 화면의 '알아 둘 점'에 있어요." }) : null)));
    }
    var spec = data.spec_label || (state.status && state.status.spec_label) || '';
    var foot = [];
    if (isNum(v.total) && v.total > 0) {
      foot.push(v.passed === v.total && unv === 0 ? '센터 공식 명세서와 ' + v.total + '개 항목 대조 완료'
        : '명세서 대조 ' + v.passed + '/' + v.total + (unv ? ' · 센터 화면에서 확인할 것 ' + unv + '개' : ''));
    }
    if (spec) foot.push(txt(spec));
    if (foot.length) sheet.appendChild(el('p', { class: 'ps-foot', text: foot.join(' · ') }));
  }

  /* ---------- 받은 파일 ---------- */

  var NO_COMMA = /연월|년월|기준월|연도|년도|코드|번호|ym|month|year|_cd$|^no$/i;
  var IS_PCT = /비중|비율|%|pct|share/i;

  function cellText(v, colName) {
    if (v === null || v === undefined || v === '') return '-';
    if (!isNum(v)) return txt(v);
    if (IS_PCT.test(colName)) return fmtNum(v, 1) + (colName.indexOf('%') >= 0 ? '' : '%');
    if (NO_COMMA.test(colName)) return String(v);
    return fmtNum(v);
  }

  function tableCells(t) {
    var cols = arr(t.columns).map(function (c) { return (c !== null && typeof c === 'object') ? { key: c.key !== undefined ? c.key : (c.en || c.label), label: txt(c.label || c.ko || c.key || c.en) } : { key: c, label: txt(c) }; });
    var cells = arr(t.rows).map(function (r) {
      if (Array.isArray(r)) return r;
      if (r !== null && typeof r === 'object') return cols.map(function (c) { return r[c.key] !== undefined ? r[c.key] : r[c.label]; });
      return [r];
    });
    return { cols: cols, cells: cells };
  }

  /* 원래 숫자 표. 가린 칸("10 미만", "가림")은 빗금 배경 + 글자 그대로 */
  function resultTable(t) {
    var tc = tableCells(t), cols = tc.cols, cells = tc.cells;
    var age = isAgeTable(t) ? ageNamer(cols.map(function (c) { return c.label; }).concat(cells.map(function (r) { return r[0]; }))) : null;
    if (age) {
      cols = cols.map(function (c) { return { key: c.key, label: age(c.label) }; });
      cells = cells.map(function (r) { return [typeof r[0] === 'string' ? age(r[0]) : r[0]].concat(r.slice(1)); });
    }
    /* 화면 표에서는 "NA(알 수 없음)" 을 "알 수 없음" 으로(인쇄·근거 원문은 서버 글 그대로) */
    cols = cols.map(function (c) { return { key: c.key, label: naLabel(c.label) }; });
    cells = cells.map(function (r) { return [typeof r[0] === 'string' ? naLabel(r[0]) : r[0]].concat(r.slice(1)); });
    var numeric = cols.map(function (c, i) {
      return cells.some(function (r) { return isNum(r[i]); }) || cells.some(function (r) { return typeof r[i] === 'string' && r[i].indexOf('미만') >= 0; });
    });
    var tb = el('tbody');
    cells.forEach(function (r) {
      tb.appendChild(el('tr', null, cols.map(function (c, i) {
        var v = r[i], masked = numeric[i] && typeof v === 'string' && v !== '' && v !== '-';
        return el(i === 0 ? 'th' : 'td', { scope: i === 0 ? 'row' : null, class: ((numeric[i] && i > 0) ? 'num' : '') + (masked ? ' masked' : '') || null, text: cellText(v, c.label) });
      })));
    });
    return el('div', { class: 'table-wrap' }, el('table', { class: 'data-table' },
      el('thead', null, el('tr', null, cols.map(function (c, i) { return el('th', { scope: 'col', class: numeric[i] && i > 0 ? 'num' : null, text: c.label }); }))), tb));
  }

  function splitTitle(s) {
    s = txt(s);
    var k = s.indexOf(' · ');
    return k < 0 ? { main: s, rest: '' } : { main: s.slice(0, k), rest: s.slice(k + 3) };
  }

  /* 연령대 값 "10, 20 … 70" 을 "10대 이하, 20대 … 70대 이상" 으로(가장 작은 값은 이하, 가장 큰 값은 이상) */
  function ageNamer(values) {
    var ns = values.map(function (v) { var m = /^(\d)0$/.exec(txt(v)); return m ? Number(m[1]) : null; }).filter(function (n) { return n !== null; });
    if (ns.length < 2) return function (v) { var m = /^(\d)0$/.exec(txt(v)); return m ? txt(v) + '대' : txt(v); };
    var lo = Math.min.apply(null, ns), hi = Math.max.apply(null, ns);
    return function (v) {
      var m = /^(\d)0$/.exec(txt(v));
      if (!m) return txt(v);
      var n = Number(m[1]);
      return n + '0대' + (n === lo ? ' 이하' : (n === hi ? ' 이상' : ''));
    };
  }
  function isAgeTable(t) { return /연령대/.test(txt(t.title)); }
  function isNaName(v) { return /^NA/.test(txt(v)); }
  function naLabel(v) { var s = txt(v); var m = /^NA\s*\(([^()]+)\)$/.exec(s); return m ? m[1] : (s === 'NA' ? '알 수 없음' : s); }

  function barCols(t) {
    var tc = tableCells(t), cols = tc.cols, cells = tc.cells;
    var nameCol = -1, valCol = -1;
    for (var i = 0; i < cols.length; i++) {
      if (nameCol < 0 && !cells.some(function (r) { return isNum(r[i]); }) && !IS_PCT.test(cols[i].label)) nameCol = i;
      if (valCol < 0 && IS_PCT.test(cols[i].label)) valCol = i;
    }
    if (nameCol < 0) nameCol = 0;
    return { cols: cols, cells: cells, nameCol: nameCol, valCol: valCol };
  }

  /* "가장 많음" 줄: 서버가 매긴 순위 1위 줄. 순위 열이 없으면 순위에 든 줄(NA 제외)에서 가장 큰 값 */
  function topRow(bc) {
    var cols = bc.cols, ranked = bc.cells.filter(function (r) { return !isNaName(r[bc.nameCol]); });
    var rankCol = -1, top = null;
    cols.forEach(function (c, k) { if (rankCol < 0 && c.label.indexOf('순위') >= 0) rankCol = k; });
    if (rankCol >= 0) ranked.forEach(function (r) { if (!top && r[rankCol] === 1) top = r; });
    if (!top && bc.valCol >= 0) {
      var best = -Infinity;
      ranked.forEach(function (r) { if (isNum(r[bc.valCol]) && r[bc.valCol] > best) { best = r[bc.valCol]; top = r; } });
    }
    return top;
  }

  /* 표 밑 설명(서버 글)의 "오전 07~11시, … 밤 22시~07시" 에서 이름별 시간 범위를 읽는다(앞 0 은 뗀다) */
  function timeRanges(note) {
    var out = {}, re = /([가-힣]+)\s+(\d{1,2})시?\s*~\s*(\d{1,2})시/g, m;
    var s = txt(note);
    while ((m = re.exec(s))) out[m[1]] = Number(m[2]) + '~' + Number(m[3]) + '시';
    return out;
  }

  /* 비율·순위 표를 막대 목록으로. opt.big: 대표 막대(제목 없음). opt.label: 그림 전체의 화면 읽기 글 */
  function barList(t, opt) {
    opt = opt || {};
    var bc = barCols(t), cols = bc.cols, cells = bc.cells, nameCol = bc.nameCol, valCol = bc.valCol;
    var age = isAgeTable(t) ? ageNamer(cells.map(function (r) { return r[nameCol]; })) : function (v) { return txt(v); };
    var tt = splitTitle(t.title);
    var block = el('section', { class: 'bar-block' }, opt.big ? null : el('h3', { text: ymInText(tt.main) }));
    var hasNa = false;
    if (valCol >= 0 && cells.length) {
      var ranked = [], extra = [];
      cells.forEach(function (r) { (isNaName(r[nameCol]) ? extra : ranked).push(r); });
      hasNa = extra.length > 0;
      var vals = cells.map(function (r) { return r[valCol]; }).filter(isNum);
      var max = Math.max.apply(null, ranked.map(function (r) { return r[valCol]; }).filter(isNum).concat([0]));
      /* 합이 100% 를 넘지 않는 비중이면 막대 끝은 100%(목업 5: 42% 는 절반쯤). 그 밖의 값은 가장 큰 값 기준 */
      var sumAll = vals.reduce(function (a, b) { return a + b; }, 0);
      var whole = sumAll > 0 && sumAll <= 101 && max <= 100 ? 100 : max;
      var top = topRow(bc);
      var limit = opt.big ? 8 : 12;
      var shown = ranked.slice(0, limit);
      if (top && shown.indexOf(top) < 0) shown.push(top);
      var ul = el('ul', { class: 'bars' }, shown.map(function (r) {
        var v = r[valCol], isTop = r === top && max > 0;
        var w = isNum(v) && whole > 0 ? Math.max(1, Math.min(100, v / whole * 100)) : 0;
        /* "노형동 (요청한 곳)" 은 이름 칸이 길어지지 않게 이름 아래 작은 꼬리표로 */
        var nm = age(r[nameCol]), mine = /\s*\(요청한 곳\)\s*$/.test(nm);
        if (mine) nm = nm.replace(/\s*\(요청한 곳\)\s*$/, '');
        return el('li', { class: (isTop ? 'top' : '') + (mine ? ' mine' : '') || null },
          el('span', { class: 'bar-name', title: txt(r[nameCol]) }, el('span', { text: nm }), mine ? el('span', { class: 'bar-tag', text: '요청한 곳' }) : null),
          el('span', { class: 'bar-track', 'aria-hidden': 'true' }, w ? el('span', { class: 'bar-fill', style: 'width:' + w.toFixed(1) + '%' }) : null),
          el('span', { class: 'bar-val' + (isNum(v) ? '' : ' masked') }, isNum(v) ? fmtNum(v, 1) + '%' : cellText(v, cols[valCol].label),
            isTop ? el('span', { class: 'sr-only', text: ' 가장 많음' }) : null));
      }));
      if (!opt.big) block.appendChild(el('p', { class: 'chart-cap', text: '막대: ' + cols[valCol].label + (ranked.length > shown.length ? ' · 앞 ' + shown.length + '줄(나머지는 표로 보기)' : '') }));
      if (opt.label) block.appendChild(el('div', { class: 'bars-img', role: 'img', 'aria-label': opt.label + ' 막대 그림. 같은 값이 "자세히 보기"의 표에 있어요.' }, ul));
      else block.appendChild(ul);
    }
    if (!opt.big) {
      block.appendChild(el('details', { class: 'chart-table' }, el('summary', { text: '표로 보기' }), resultTable(t)));
      if (hasNa) block.appendChild(el('p', { class: 'chart-cap', text: isAgeTable(t) ? '연령대를 알 수 없는 줄은 순위에서 뺐어요.' : '알 수 없음으로 적힌 줄은 순위에서 뺐어요.' }));
      if (t.note) block.appendChild(el('p', { class: 'chart-cap', text: firstSentence(easy(t.note)) }));
    }
    return block;
  }

  var LEVEL = { error: ['오류', 'bad'], '오류': ['오류', 'bad'], warn: ['주의', 'warn'], warning: ['주의', 'warn'], '주의': ['주의', 'warn'], info: ['안내', 'info'], '안내': ['안내', 'info'], ok: ['확인', 'ok'] };

  function runDatasetNos() {
    var o = state.run && state.run.order, nos = {};
    if (!o) return null;
    list(o.items).forEach(function (it) { if (it && it.dataset_no) nos[it.dataset_no] = 1; });
    arr(o.alternatives).forEach(function (a) { arr(a && a.items).forEach(function (it) { if (it && it.dataset_no) nos[it.dataset_no] = 1; }); });
    return nos;
  }

  /* 이 파일이 어느 빈칸을 채웠는가: 채운 표의 제목으로 가린다 */
  function gapFilledBy(key, r) {
    var titles = arr(r.tables).map(function (t) { return txt(t.title); });
    var has = function (head) { return titles.some(function (t) { return t.indexOf(head) === 0; }); };
    var sc = r.scope || {};
    var nos = runDatasetNos(), rec = r.recognized || {};
    if (nos && rec.dataset_no && !nos[rec.dataset_no]) return false;
    if (key === 'slot') return has('시간대별');
    if (key === 'dow') return has('요일별');
    if (key === 'age') return has('연령대별');
    if (key === 'user_type' || key === 'who') return has('이용자구분별');
    if (key === 'dong') return !!sc.region && String(sc.region).indexOf('전체') < 0;
    if (key === 'biz') return !!sc.biz && String(sc.biz).indexOf('업종 ') !== 0;
    return false;
  }

  function filledLabels(r) {
    if (!state.run) return [];
    return arr(state.run.gaps).filter(function (g) { return g && txt(g.status).indexOf('없') < 0 && gapFilledBy(g.key, r); }).map(function (g) { return txt(g.label || g.key); });
  }

  /* 대표 표: "응용" 표 가운데 빈칸(gaps) 순서대로 그 기준의 표. 없으면 headline 이 있는 첫 응용 표, 그것도 없으면 첫 응용 표 */
  var GAP_HEAD = { slot: '시간대별', dow: '요일별', age: '연령대별', who: '이용자구분별', user_type: '이용자구분별' };

  function pickMainTable(r) {
    var app = arr(r.tables).filter(function (t) { return t && txt(t.kind).indexOf('응용') >= 0; });
    if (!app.length) return null;
    var gaps = state.run ? list(state.run.gaps).filter(function (g) { return txt(g.status).indexOf('없') < 0; }) : [];
    for (var i = 0; i < gaps.length; i++) {
      var head = GAP_HEAD[txt(gaps[i].key)];
      if (!head) continue;
      for (var k = 0; k < app.length; k++) if (txt(app[k].title).indexOf(head) === 0) return app[k];
    }
    for (var j = 0; j < app.length; j++) if (app[j].headline) return app[j];
    return app[0];
  }

  /* 측정값 이름을 사장님 말로(카드 데이터는 "카드 결제 건수"). 모르는 이름은 그대로 */
  var METRIC_KO = { '이용건수': '결제 건수', '이용금액': '결제 금액', '이용자수': '이용자 수', '방문인구': '방문 인구' };
  function metricWords(metric, dsNameText) {
    var m = String(metric || '').trim();
    var ko = METRIC_KO[m.replace(/\s+/g, '')];
    if (!ko) return m;
    if (/카드/.test(dsNameText) && /결제/.test(ko)) return '카드 ' + ko;
    return ko;
  }

  /* 한 문장 요약. 숫자는 모두 대표 표의 1위 줄 값(서버 값). 넣은 파일의 달(서버 값)을 앞에 밝힌다 */
  function summarySentence(t, ctx) {
    if (!t) return null;
    ctx = ctx || {};
    var bc = barCols(t);
    if (bc.valCol < 0 || !bc.cells.length) return null;
    var top = topRow(bc);
    if (!top || !isNum(top[bc.valCol])) return null;
    var nameRaw = top[bc.nameCol];
    var name = isAgeTable(t) ? ageNamer(bc.cells.map(function (r) { return r[bc.nameCol]; }))(nameRaw) : txt(nameRaw);
    var head = splitTitle(t.title).main;
    var metric = bc.cols[bc.valCol].label.replace(/\s*비중\s*\(%\)\s*$/, '').replace(/\s*\(%\)\s*$/, '').replace(/\s*비중\s*$/, '');
    var mko = metricWords(metric, ctx.dsName || '');
    var range = timeRanges(t.note)[txt(nameRaw)];
    var when = ctx.month && parseYm(ctx.month) ? fmtYmKo(ctx.month) + ', ' : '';
    var who = range ? josa(range, '이가') + ' ' : '';
    return {
      title: josa(name, '이가') + ' 가장 많아요',
      sub: when + who + (mko ? mko + '의 ' : '전체의 ') + fmtPct(top[bc.valCol]) + '예요.',
      chartTitle: head
    };
  }

  function resetCsvResults() {
    state.csv = [];
    state.csvErrors = [];
  }

  /* 받을 목록대로라면 넣을 파일(자료 × 달). 넣은 파일의 자료와 달이 맞으면 표시한다 */
  function waitRows() {
    var items = state.run ? list(state.run.order && state.run.order.items) : [];
    var rows = [];
    items.forEach(function (it) { list(it.months).forEach(function (m) { rows.push({ no: txt(it.dataset_no), name: dsName(it.dataset_name), ym: fmtYm(m.ym) }); }); });
    rows.forEach(function (w) {
      w.got = state.csv.some(function (c) { var rc = c.r.recognized || {}; return txt(rc.dataset_no) === w.no && fmtYm(c.r.month) === w.ym; });
    });
    return rows;
  }

  function diagList(r) {
    return arr(r.diagnostics).map(function (d) {
      if (d === null || typeof d !== 'object') return { lv: LEVEL.info, text: txt(d) };
      return { lv: LEVEL[String(d.level || '').toLowerCase()] || [txt(d.level) || '안내', 'info'], text: txt(d.detail) };
    });
  }

  /* 결과 화면의 주의 띠: 결정에 걸리는 것만 한 줄씩 */
  function fileCautions(r, k) {
    var out = [];
    var rec = r.recognized || {};
    var nos = runDatasetNos();
    var more = el('a', { class: 'link', href: '#/file/detail/' + k }, '자세히 ›');
    if (rec.dataset_no && nos && !nos[rec.dataset_no]) out.push('받을 목록과 다른 자료예요. 결과는 넣은 파일 기준이에요.');
    arr(rec.ask).map(txt).filter(Boolean).forEach(function (s) { out.push(firstSentence(easy(s))); });
    diagList(r).filter(function (d) { return d.lv[1] === 'bad' || d.lv[1] === 'warn'; }).forEach(function (d) { out.push(firstSentence(easy(d.text))); });
    if ((r.recoverable || r.small_visible) && arr(r.tables).length) out.push('가리지 않은 작은 값이 있어요. 센터 밖으로 가져가기 전에 확인하세요.');
    return out.map(function (s, i) { return el('div', { class: 'mini-band warn' }, icon('alert'), el('div', null, s + ' ', i === 0 ? more.cloneNode(true) : null)); });
  }

  function renderFile(kArg) {
    var n = state.csv.length;
    var k = kArg ? Number(kArg) : n;
    var item = state.csv[k - 1];
    var back = $('fileBack');
    if (kArg) { back.setAttribute('href', '#/file'); back.lastChild.textContent = '파일 넣기'; }
    else if (state.run) { back.setAttribute('href', '#/list'); back.lastChild.textContent = '받을 목록'; }
    else { back.setAttribute('href', '#/home'); back.lastChild.textContent = '처음 화면'; }

    var head = clear($('fileHead')), top = clear($('fileBodyTop')), res = clear($('fileResult')), foot = clear($('fileFoot'));
    var zone = $('dropZone');
    zone.hidden = !!item && !state.csvBusy;
    res.hidden = state.csvBusy;

    if (!item) {
      $('fileTitle').textContent = '받은 파일을 넣어 주세요';
      $('fileSub').textContent = '센터 PC에서 내려받은 파일을 그대로 넣으면 돼요.';
      if (!state.run) top.appendChild(el('div', { class: 'mini-band' }, icon('info'), el('div', { text: '받을 목록 없이 넣으면 제주도 전체로 계산해요.' })));
    } else {
      var r = item.r, rec = r.recognized || {}, known = !!rec.dataset_no;
      var sc = r.scope || {};
      var main = known ? pickMainTable(r) : null;
      var sum = summarySentence(main, { month: sc.month || r.month, dsName: txt(rec.dataset_name) });
      head.appendChild(el('p', { class: 'ok-line' }, icon('check-circle'), el('span', { text: '파일 ' + n + '개를 읽었어요' }),
        r.virtual ? badge('plain', 'info', '연습용 가상 파일') : null));
      var diags = diagList(r);
      var loud = diags.filter(function (d) { return d.lv[1] === 'bad' || d.lv[1] === 'warn'; });
      if (!known) {
        $('fileTitle').textContent = '어떤 자료인지 알아보지 못했어요';
        $('fileSub').textContent = loud[0] ? firstSentence(easy(loud[0].text)) : '자세히 보기에서 파일 점검 기록을 확인하세요.';
      } else if (sum) {
        $('fileTitle').textContent = sum.title;
        $('fileSub').textContent = sum.sub;
      } else {
        $('fileTitle').textContent = '이 파일로 표를 만들지 못했어요';
        $('fileSub').textContent = loud[0] ? firstSentence(easy(loud[0].text)) : '자세히 보기에서 파일 점검 기록을 확인하세요.';
      }
      var cautions = fileCautions(r, k);
      append(top, [cautions.length ? el('div', { class: 'list-notes' }, cautions) : null]);
      if (main && sum) res.appendChild(el('div', { class: 'file-main' }, barList(main, { big: true, label: sum.title + '. ' + sum.sub })));
      res.appendChild(actions([el('a', { class: 'btn-main', href: '#/file/detail/' + k }, '자세히 보기'),
        el('button', { type: 'button', class: 'btn-sub', onclick: function () { $('csvFile').click(); } }, icon('upload'), '파일 더 넣기')]));
      if (n > 1) {
        res.appendChild(el('h2', { class: 'h2', text: '다른 파일 결과' }));
        res.appendChild(el('ul', { class: 'slip' }, state.csv.map(function (c, i) {
          if (i === k - 1) return null;
          var rc = c.r.recognized || {};
          return slipRow('', (rc.dataset_no ? dsName(rc.dataset_name) : c.name) + (c.r.month ? ' · ' + fmtYmKo(c.r.month) : ''), '/file/' + (i + 1));
        })));
      }
    }
    renderCsvErrors();
    var w = waitRows();
    if (w.length) {
      var got = w.filter(function (x) { return x.got; }).length;
      foot.appendChild(el('a', { class: 'link', href: '#/file/wait' }, '넣을 파일 ' + w.length + '개 보기 (' + got + '개 넣음) ›'));
    }
    foot.appendChild(el('p', { class: 'lock' }, icon('lock'), '받은 파일은 센터 밖으로 가져갈 수 없어요.'));
    if (!item) foot.appendChild(el('a', { class: 'link', href: '#/file/practice' }, '연습용 파일은 어디 있나요? ›'));
    foot.className = 'file-foot';
    fillIcons($('fileCard'));
  }

  /* 파일 오류 글: 화면에는 원인 한 줄만(쉬운 말). 읽은 칸 이름(영문)·명세서 판 표기·오류 이름은 근거 원문으로 */
  function csvErrParts(err) {
    var msg = txt(err && err.message), s = msg, raw = [];
    if (err && err.kind === 'down') return { text: msg, raw: [] };
    var cut = s.search(/\s*(읽은 컬럼|파일에만 있는 컬럼)\s*:/);
    if (cut >= 0) { raw.push(s.slice(cut).trim()); s = s.slice(0, cut); }
    s = s.replace(/명세서\(((?:[^()]|\([^()]*\))*)\)/, function (m, inner) { raw.push('기준 명세서: ' + inner); return '명세서'; });
    s = s.replace(/:\s*([A-Za-z_][\w]*(?:\s*,\s*[A-Za-z_][\w]*)*)\s*\.?$/, function (m, cols) { raw.push('빠진 칸: ' + cols); return '.'; });
    s = s.replace(/\(([A-Z][A-Za-z]*(?:Error|Exception|Warning)?)\)/g, function (m, n) { raw.push('오류 이름: ' + n); return ''; });
    if (raw.length) raw.push('원래 글: ' + msg);
    return { text: easy(s.trim()), raw: raw };
  }

  function renderCsvErrors() {
    var box = clear($('csvError'));
    var failed = state.csvErrors;
    if (!failed.length) { box.hidden = true; return; }
    if (failed.every(function (f) { return f.err.kind === 'down'; })) box.setAttribute('data-down', '1'); else box.removeAttribute('data-down');
    failed.forEach(function (f) {
      var p = csvErrParts(f.err);
      var dl = el('dl', null, el('dt', { text: '파일' }), el('dd', { text: f.name }), el('dt', { text: '원인' }), el('dd', { text: p.text }));
      if (f.err.action) append(dl, [el('dt', { text: '할 일' }), el('dd', { text: easy(f.err.action) })]);
      box.appendChild(el('div', { class: 'errorbox-in' }, illuSlot('file-bad', 'illu-bad'), el('div', { class: 'errorbox-text' }, dl,
        p.raw.length ? rawBox(rawList(p.raw)) : null)));
    });
    box.appendChild(el('button', { type: 'button', class: 'link', onclick: function () { $('csvFile').click(); } }, '다른 파일 고르기'));
    box.hidden = false;
  }

  function renderFileDetail(kArg) {
    var k = Number(kArg), item = state.csv[k - 1];
    var r = item.r, rec = r.recognized || {}, known = !!rec.dataset_no, sc = r.scope || {};
    var tables = arr(r.tables);
    var main = known ? pickMainTable(r) : null;
    var kids = [];
    var filled = filledLabels(r);
    if (filled.length && tables.length) kids.push(el('div', { class: 'info-box ok' }, icon('check-circle'), el('div', { class: 'info-box-text' },
      el('span', null, '앞에서 비어 있던 ', el('b', { text: filled.join('·') }), ' 정보를 이 파일로 채웠어요.'))));
    var tags = [];
    if (r.virtual) tags.push(badge('warn', 'info', '연습용 가상 파일'));
    tags.push(badge('ok', 'lock', 'AI로 보낸 값 ' + (isNum(r.sent_to_llm) ? fmtNum(r.sent_to_llm) : '0') + '건'));
    if (isNum(r.masked_total) && tables.length) tags.push(badge('plain', 'lock', '가린 칸 ' + fmtNum(r.masked_total) + '개'));
    if (isNum(r.rows)) tags.push(badge('plain', null, '읽은 줄 ' + fmtNum(r.rows) + '줄'));
    kids.push(el('p', { class: 'tags' }, tags));
    var app = tables.filter(function (t) { return txt(t.kind).indexOf('응용') >= 0; });
    var raw = tables.filter(function (t) { return txt(t.kind).indexOf('응용') < 0; });
    if (app.length) {
      kids.push(el('h2', { class: 'h2', text: '비율과 순위' }));
      app.forEach(function (t) { kids.push(barList(t)); });
    }
    if (!tables.length) kids.push(el('p', { class: 'body-text muted', text: '이 파일로 표를 만들지 못했어요.' }));
    /* 숫자 표와 공개 데이터 견주기는 한 단계 더 들어가는 페이지로(이 페이지에는 막대만) */
    var rc = r.reconcile;
    var more = [];
    if (raw.length) more.push(slipRow('', '자세한 숫자 표 ' + raw.length + '개', '/file/detail/' + k + '/numbers',
      { lead: 'table', note: el('span', { class: 'slip-label', text: '가린 칸 표시 포함' }) }));
    if (rc) more.push(slipRow('', '공개 데이터와 견주기', '/file/detail/' + k + '/compare', { lead: 'layers' }));
    if (more.length) kids.push(el('h2', { class: 'h2', text: '더 보기' }), el('ul', { class: 'slip' }, more));
    kids.push(rawBox([rawLine('넣은 파일', item.name), rawLine('알아본 자료', known ? txt(rec.dataset_no) + ' ' + txt(rec.dataset_name) : '알아보지 못함'),
      rawList(arr(rec.ask), '확인이 필요한 것'),
      rawList(diagList(r).map(function (d) { return d.lv[0] + ': ' + d.text; }), '파일 점검 기록'),
      rawLine('범위', sc.text), rawLine('되셈 가능한 칸', r.recoverable), rawLine('가리지 않은 10 미만 값', r.small_visible),
      rawLine('견주기 방법', rc && rc.note), rawLine('가린 칸 읽는 법', r.mask_note),
      rawList(tables.filter(function (t) { return t.note; }).map(function (t) { return splitTitle(t.title).main + ': ' + txt(t.note); }), '표 밑 설명')]));
    setDetail({ illu: 'table-doc', title: known ? dsName(rec.dataset_name) + ' 자세히' : '파일 자세히',
      sub: sc.text ? ymInText(plain(sc.text)) : (r.month ? fmtYmKo(r.month) : ''), back: state.csv.length > 1 ? '/file/' + k : '/file', body: kids });
  }

  /* 결과 자세히의 하위 페이지: 자세한 숫자 표(가린 칸 설명), 공개 데이터와 견주기 */
  function renderFileSub(kArg, kind) {
    var k = Number(kArg), item = state.csv[k - 1];
    var r = item.r, rec = r.recognized || {}, known = !!rec.dataset_no;
    var tables = arr(r.tables);
    var kids = [];
    var nm = known ? dsName(rec.dataset_name) : '넣은 파일';
    if (kind === 'numbers') {
      var raw = tables.filter(function (t) { return txt(t.kind).indexOf('응용') < 0; });
      kids.push(el('p', { class: 'chart-cap legend-mask' }, el('span', { class: 'masked mask-chip', text: '10 미만' }),
        el('span', { text: '표시: 개인을 알아볼 수 없게 작은 값과 거꾸로 셈할 수 있는 값은 가려요("10 미만", "가림").' })));
      if (r.mask_note) kids.push(el('p', { class: 'chart-cap', text: easy(r.mask_note) }));
      raw.forEach(function (t) {
        kids.push(el('section', { class: 'bar-block' }, el('h2', { class: 'h3', text: ymInText(splitTitle(t.title).main) }), resultTable(t),
          t.note ? el('p', { class: 'chart-cap', text: firstSentence(easy(t.note)) }) : null));
      });
      if (!raw.length) kids.push(el('p', { class: 'body-text muted', text: '숫자 표가 없어요.' }));
      setDetail({ illu: 'table-doc', title: '자세한 숫자', sub: nm + (r.month ? ' · ' + fmtYmKo(r.month) : ''), back: '/file/detail/' + k, body: kids });
      return;
    }
    var rc = r.reconcile || {};
    if (rc.available && isNum(rc.diff_pct)) {
      kids.push(el('ul', { class: 'slip read' },
        kvRow('이 파일 합계', isNum(rc.center_sum) ? fmtCount(rc.center_sum) : '숨김'),
        kvRow('공개 데이터(제주도 전체)', fmtCount(rc.public_value)),
        kvRow('차이', (rc.diff_pct > 0 ? '+' : '') + fmtNum(rc.diff_pct, 1) + '%')));
      if (r.virtual) kids.push(el('p', { class: 'chart-cap', text: '연습용 가상 파일은 공개 데이터를 나눠 만들어서, 이 차이로 실제 데이터의 정확도를 말할 수 없어요.' }));
    } else {
      kids.push(el('p', { class: 'body-text', text: rc.available ? '법정동이 다 들어 있지 않아 견주지 않았어요.' : '견줄 공개 데이터 값이 없어요.' }));
    }
    kids.push(rawBox([rawLine('견주기 방법', rc.note)]));
    setDetail({ illu: 'line-doc', title: '공개 데이터와 견주기', sub: '이 파일의 합계를 제주도 전체 공개 데이터와 견줬어요.', back: '/file/detail/' + k, body: kids });
  }

  function renderWaitList() {
    var w = waitRows();
    var got = w.filter(function (x) { return x.got; }).length;
    setDetail({ illu: 'drop-file', title: w.length ? '넣을 파일 ' + w.length + '개' : '받을 목록이 아직 없어요', sub: w.length ? w.length + '개 중 ' + got + '개 넣었어요.' : '질문하면 넣을 파일을 알려 드려요.', back: '/file',
      body: w.length ? [el('ul', { class: 'slip' }, w.map(function (x) {
        return slipRow('', x.name + ' · ' + fmtYmKo(x.ym), null, { lead: x.got ? 'check-circle' : 'none-circle', leadCls: x.got ? 'ok' : 'off',
          note: el('span', { class: 'slip-label', text: x.got ? '넣음' : '아직' }) });
      }))] : [el('a', { class: 'btn-main', href: '#/home' }, '질문하러 가기')] });
  }

  function renderPractice() {
    setDetail({ illu: 'drop-file', title: '연습용 파일로 해 보기', sub: '프로그램에 연습용 가상 파일이 들어 있어요.', back: '/file', body: [
      el('ol', { class: 'num-list' },
        el('li', { text: '받은 파일 화면에서 [파일 고르기]를 눌러요.' }),
        el('li', { text: '파일 고르기 창에서 이 프로그램 폴더를 열어요.' }),
        el('li', { text: '그 안의 data 폴더, sample_center 폴더를 차례로 열어요.' }),
        el('li', { text: 'Results 로 시작하는 파일을 골라요. 여러 개를 함께 골라도 돼요.' })),
      el('ul', { class: 'dot-list' },
        el('li', { text: '다시 계산할 수 있는 파일은 카드 이용 현황 3가지(읍면동·요일별·시간대)예요.' }),
        el('li', { text: '파일 이름은 바뀌어도 돼요. 파일 첫 줄(칸 이름)로 어떤 자료인지 알아봐요.' }),
        el('li', { text: '결과를 센터 밖으로 가져가려면 화면을 캡처한 그림으로 반출 신청을 해요.' })),
      el('a', { class: 'btn-main', href: '#/file' }, '파일 넣으러 가기'),
      rawBox(el('p', { text: '카드 이용 현황 3가지의 명세서 번호는 1-08(읍면동), 1-12(요일별), 1-14(시간대)예요.' }))] });
  }

  /* 동네 미리 보기: 공개 Open API(허브 유동인구·상가정보·소상공인365). 읍면동 이름과 업종 코드만 보낸다 */
  function localBox(items) {
    var it = items && items[0], emd = '', biz = '';
    arr(it && it.conditions).forEach(function (c) {
      var ko = txt(c.column_ko);
      if (!emd && /법정동|행정동|읍면동/.test(ko)) emd = txt(c.value);
      if (!biz && c.column === 'biz_cd') biz = txt(c.value);
    });
    var box = el('section', { class: 'local-box', 'aria-label': '동네 미리 보기' });
    if (!emd) { box.hidden = true; return box; }
    box.appendChild(el('p', { class: 'local-title', text: emd + ' 미리 보기 · 공개 API' }));
    var body = el('div', { class: 'local-body', text: '불러오는 중이에요…' });
    box.appendChild(body);
    api('GET', '/api/local?emd=' + encodeURIComponent(emd) + '&biz=' + encodeURIComponent(biz)).then(function (d) {
      clear(body);
      var rows = [];
      if (d.store) rows.push(el('p', null, el('b', { text: d.store.upjong + ' 상가 ' + d.store.count.toLocaleString() + '곳' }), el('span', { class: 'local-src', text: ' · 소상공인시장진흥공단 상가정보' })));
      if (d.hub) {
        var sh = d.hub.share;
        rows.push(el('p', null, el('b', { text: '방문이 가장 많은 나이 ' + d.hub.top_visit_age + ' (' + d.hub.top_visit_age_share + '%)' }),
          el('span', { class: 'local-src', text: ' · 거주 ' + sh.resd + '% · 근무 ' + sh.work + '% · 방문 ' + sh.visit + '% (' + d.hub.date + ', 제주데이터허브 유동인구)' })));
      }
      if (d.sbiz365_url) rows.push(el('p', null, el('button', { type: 'button', class: 'link', onclick: function () { window.open(d.sbiz365_url, '_blank', 'noopener'); } }, '소상공인365 간단분석에서 상권 더 보기 ›')));
      if (!rows.length) { box.hidden = true; return; }
      append(body, rows);
    }, function () { box.hidden = true; });
    return box;
  }

  /* 샘플로 해 보기: 동봉한 연습용 가상 CSV를 서버에서 받아 "파일 넣기"와 같은 길로 보낸다 */
  function useSampleCsv() {
    if (state.csvBusy) return;
    var ds = '';
    try { ds = (state.run && state.run.order && state.run.order.items && state.run.order.items[0] && state.run.order.items[0].dataset_no) || ''; } catch (e) { ds = ''; }
    fetch('/api/sample-csv' + (ds ? '?dataset=' + encodeURIComponent(ds) : ''), { cache: 'no-store' }).then(function (res) {
      if (!res.ok) throw new Error('HTTP ' + res.status);
      var name = decodeURIComponent(res.headers.get('X-Filename') || 'sample.csv');
      return res.blob().then(function (b) { uploadFiles([new File([b], name, { type: 'text/csv' })]); });
    }).catch(function () {
      if (state.path !== '/file') go('/file');
      showError($('csvError'), { message: '연습용 샘플 파일을 불러오지 못했어요.', action: 'data/sample_center/ 폴더가 있는지 확인해 주세요.' });
    });
  }

  function useSampleAll() {
    var first = document.querySelector('#examples .chip');
    if (!first || state.busy) return;
    first.click();
    submitPlan('text');
  }

  function uploadFiles(files) {
    files = Array.prototype.slice.call(files || []);
    if (!files.length || state.csvBusy) return;
    state.csvBusy = true;
    state.csvErrors = [];
    if (state.path !== '/file') go('/file');
    $('csvFile').disabled = true;
    $('csvError').hidden = true;
    $('dropZone').hidden = false;
    $('fileResult').hidden = true;
    $('csvProgress').hidden = false;
    $('dropZone').classList.add('busy');
    var okN = 0;
    var chain = Promise.resolve();
    files.forEach(function (f, i) {
      chain = chain.then(function () {
        $('csvProgressText').textContent = '파일을 읽고 있어요 (' + (i + 1) + '/' + files.length + ')';
        var q = state.run && state.run.run_id ? '?run_id=' + encodeURIComponent(state.run.run_id) : '';
        return api('POST', '/api/center-csv' + q, f, { 'Content-Type': 'application/octet-stream', 'X-Filename': encodeURIComponent(f.name) }).then(function (r) {
          state.csv.push({ name: f.name, r: r });
          okN += 1;
        }, function (err) {
          state.csvErrors.push({ name: f.name, err: err });
        });
      });
    });
    chain.then(function () {
      state.csvBusy = false;
      $('csvFile').disabled = false;
      $('csvFile').value = '';
      $('csvProgress').hidden = true;
      $('dropZone').classList.remove('busy');
      if (state.path === '/file') render('/file', 'force'); else go('/file');
      var failed = state.csvErrors.length;
      /* 읽지 못한 파일이 있으면 오류 상자(제목 바로 아래)로 초점을 옮겨, 앞 결과가 남아 있어도 실패를 놓치지 않게 한다 */
      if (failed) {
        var eb = $('csvError');
        eb.setAttribute('tabindex', '-1');
        eb.focus({ preventScroll: true });
        eb.scrollIntoView({ block: 'nearest' });
      }
      say('파일 ' + files.length + '개 가운데 ' + okN + '개를 읽었어요.' + (failed ? ' ' + failed + '개는 읽지 못했어요.' : '') + (okN ? ' ' + $('fileTitle').textContent + '.' : ''));
    });
  }

  /* ---------- 결과 지우기 ---------- */

  function resetPanels() {
    state.run = null;
    state.rendered = null;
    state.purpose = '';
    state.lastTrigger = {};
    $('chartTip').hidden = true;
    renderPrintSheet(null);
    $('planError').hidden = true;
    $('pickError').hidden = true;
    $('purposeText').value = '';
    markExample(null);
    resetCsvResults();
  }

  /* 앱 안의 확인 카드(제목 한 줄, 설명 한 줄, 지우기·취소). <dialog> 가 바깥을 막고 Esc 는 취소로 닫는다 */
  function askClear() {
    var dlg = $('confirmDlg');
    if (!dlg || typeof dlg.showModal !== 'function') {
      if (window.confirm('처음부터 다시 할까요? 화면의 결과와 넣은 파일, 기록을 모두 지워요. 되돌릴 수 없어요.\n(센터에서 받은 원본 파일은 그대로 둬요.)')) clearAll();
      return;
    }
    if (dlg.open) return;
    dlg.returnValue = '';
    dlg.showModal();
    $('confirmCancel').focus();
  }

  function clearAll() {
    var btn = $('clearBtn'), msg = $('globalMsg');
    btn.disabled = true;
    msg.hidden = true;
    $('globalError').hidden = true;
    api('POST', '/api/clear', '{}', { 'Content-Type': 'application/json; charset=utf-8' }).then(function (r) {
      var d = (r && r.deleted) || {};
      resetPanels();
      if (state.status) state.status.leftover = { files: 0, results: 0 };
      if (state.path === '/home') render('/home', 'force'); else go('/home', { replace: true });
      showMsg('ok', '지웠어요: 결과 ' + fmtCount(d.results || 0) + '건, 넣은 파일 ' + fmtCount(d.files || 0) + '개, 기록 ' + fmtCount(d.logs || 0) + '건.');
      say(msg.textContent);
    }, function (err) {
      showError($('globalError'), err);
    }).then(function () { btn.disabled = false; });
  }

  /* ---------- 인쇄: 주문서를 A4 한 장에 맞춘다 ---------- */

  var printRule = null;

  function findPrintRule() {
    try {
      for (var i = 0; i < document.styleSheets.length; i++) {
        var rules = document.styleSheets[i].cssRules;
        for (var k = 0; k < rules.length; k++) {
          if (rules[k].media && rules[k].media.mediaText === 'print') return rules[k];
        }
      }
    } catch (e) { /* 규칙을 읽지 못하면 인쇄용 CSS 기본 크기로 인쇄한다 */ }
    return null;
  }

  function beforePrint() {
    if (printRule) return;
    renderPrintSheet(state.run);            /* 만든 때를 인쇄 순간으로 다시 적는다 */
    var rule = findPrintRule();
    if (!rule) return;
    var root = document.documentElement, panel = $('orderPanel');
    printRule = rule;
    rule.media.mediaText = 'all';           /* 인쇄용 규칙을 화면에 잠깐 적용해 높이를 잰다 */
    var limit = (297 - 22) * 96 / 25.4 - 12; /* A4 높이에서 위아래 여백을 뺀 값(px) */
    var size = 16, min = 6.2;                 /* 큰 글자에서 시작해 한 장에 들어갈 때까지 줄인다(빈 곳이 덜 남게) */
    root.style.setProperty('--print-fs', size + 'pt');
    while (size > min && panel.getBoundingClientRect().height > limit) {
      size = Math.round((size - 0.2) * 10) / 10;
      root.style.setProperty('--print-fs', size + 'pt');
    }
  }

  function afterPrint() {
    if (!printRule) return;
    printRule.media.mediaText = 'print';
    printRule = null;
    document.documentElement.style.removeProperty('--print-fs');
  }

  /* ---------- 시작 ---------- */

  var DETAIL = { data: renderDetailData, months: renderDetailMonths, conditions: renderDetailConditions, public: renderDetailPublic,
    nodata: renderDetailNoData, oos: renderDetailOos, trust: renderTrust };
  var RENDER = {
    home: renderHomeNote,
    pick: function () {
      /* 문장으로 만든 목록이 있으면, 직접 고르기 칸을 그 목록이 읽은 값으로 채워 둔다(앞 결과·예시 값이 남지 않게) */
      if (state.run && state.pickFor !== state.run && state.inputKind !== 'slots') {
        prefillSlots(state.run.need);
      }
      state.pickFor = state.run;
    },
    help: function () {},
    status: renderStatusPage,
    loading: function () {},
    list: renderList,
    detail: function (arg) { DETAIL[arg](); },
    alt: renderAlts,
    trust: renderTrustPage,
    file: function () { renderFile(null); },
    fileK: function (arg) { renderFile(arg); },
    fileDetail: renderFileDetail,
    fileSub: function (arg, path, kind) { renderFileSub(arg, kind); },
    fileWait: renderWaitList,
    filePractice: renderPractice
  };

  function loadAll() {
    var a = loadStatus();
    var b = api('GET', '/api/options').then(renderOptions, function (err) {
      if (err.kind !== 'down') showError($('planError'), { message: '고를 수 있는 목록을 불러오지 못했어요. ' + err.message, action: err.action || '화면을 새로 고치세요(F5).' });
    });
    return Promise.all([a, b]);
  }

  function init() {
    fillIcons(document);
    fillIllus(document);

    $('planForm').addEventListener('submit', function (e) { e.preventDefault(); submitPlan('text'); });
    $('pickForm').addEventListener('submit', function (e) { e.preventDefault(); submitPlan('slots'); });
    $('purposeText').addEventListener('keydown', function (e) {
      /* 한글 조합 중의 Enter 는 글자 확정이라 넘긴다 */
      if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.keyCode !== 229) { e.preventDefault(); submitPlan('text'); }
    });
    $('purposeText').addEventListener('input', function () { markExample(null); });

    /* 같은 창 페이지 전환: 주소가 #/ 로 시작하는 링크는 모두 여기서 받는다(새 탭 없음) */
    document.addEventListener('click', function (e) {
      if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      var a = e.target.closest ? e.target.closest('a[href^="#"]') : null;
      if (!a) return;
      var href = a.getAttribute('href');
      if (a.id === 'skipLink') {
        e.preventDefault();
        var p = document.querySelector('main > .page:not([hidden]) h1');
        if (p) p.focus();
        return;
      }
      if (href.indexOf('#/') !== 0) return;
      e.preventDefault();
      if (a.classList.contains('back')) { goBack(href.slice(1)); return; }
      state.lastTrigger[state.path] = href;
      go(href.slice(1));
    });
    window.addEventListener('popstate', function (e) {
      var p = pathFromHash();
      if (!p) return;
      if (e.state && isNum(e.state.seq)) state.navSeq = e.state.seq;
      render(p, 'pop');
    });
    window.addEventListener('hashchange', function () {
      var p = pathFromHash();
      if (!p || p === state.path) return;
      var seq = ++state.navSeq;
      try { history.replaceState({ path: p, seq: seq }, '', '#' + p); } catch (e) { /* 무시 */ }
      state.stack = state.stack || [];
      state.stack[seq] = p;
      render(p, 'push');
    });

    window.addEventListener('scroll', function () { $('chartTip').hidden = true; }, { passive: true });
    window.addEventListener('beforeprint', beforePrint);
    window.addEventListener('afterprint', afterPrint);
    $('retryBtn').addEventListener('click', function () {
      loadAll().then(function () {
        if ($('downBanner').hidden) { clearDownErrors(); say('프로그램과 다시 연결했어요.'); }
      });
    });
    $('clearBtn').addEventListener('click', askClear);
    $('confirmDlg').addEventListener('close', function () {
      if ($('confirmDlg').returnValue === 'ok') clearAll();
      else say('지우지 않았어요.');
    });
    /* 카드 바깥(어두운 곳)을 누르면 취소 */
    $('confirmDlg').addEventListener('click', function (e) { if (e.target === $('confirmDlg')) $('confirmDlg').close('cancel'); });

    $('csvFile').addEventListener('change', function (e) { uploadFiles(e.target.files); });
    $('sampleBtn').addEventListener('click', useSampleAll);
    $('sampleCsvBtn').addEventListener('click', useSampleCsv);
    /* 끌어 놓기는 받은 파일 페이지 어디서나 받는다 */
    var zone = $('dropZone'), area = $('page-file'), depth = 0;
    area.addEventListener('dragenter', function (e) { e.preventDefault(); depth += 1; zone.classList.add('over'); });
    area.addEventListener('dragleave', function () { depth = Math.max(0, depth - 1); if (!depth) zone.classList.remove('over'); });
    area.addEventListener('drop', function (e) {
      e.preventDefault();
      depth = 0;
      zone.classList.remove('over');
      uploadFiles(e.dataTransfer && e.dataTransfer.files);
    });
    /* 칸 밖에 떨어뜨려도 브라우저가 파일을 열어 화면이 바뀌지 않게 한다 */
    window.addEventListener('dragover', function (e) { e.preventDefault(); });
    window.addEventListener('drop', function (e) { e.preventDefault(); });

    renderPrintSheet(null);
    var start = pathFromHash() || '/home';
    var r0 = resolve(start);
    /* 새로 열면 결과가 메모리에 없다. 결과가 있어야 하는 주소로 열었으면 처음 화면으로 오고 한 줄로 알린다 */
    var lost = !!r0 && (r0.needRun || r0.kind === 'fileK' || r0.kind === 'fileDetail' || r0.kind === 'fileSub');
    if (!r0 || lost || r0.kind === 'loading') start = '/home';
    state.navSeq = 0;
    state.stack = [start];
    try { history.replaceState({ path: start, seq: 0 }, '', '#' + start); } catch (e) { /* 무시 */ }
    render(start, 'init');
    if (lost) showMsg('info', '보던 결과가 없어 처음 화면으로 왔어요.');

    loadAll();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
