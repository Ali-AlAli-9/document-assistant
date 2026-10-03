// Turn every server reply into a message that says what to DO next.
//
// Why this exists: the UI used to show whatever `data.detail` happened to
// contain. That meant English server strings ("File too large. Max 25MB"),
// bare status codes ("Request failed: 401"), and refusals with no route out
// ("تعذّر إنشاء الحساب، حاول مرة أخرى" leaves the user guessing).
//
// Two rules this module must never break:
//
// 1. NO NEW INFORMATION. Every hint here is derived from the status code and
//    a string the server already chose to send. A name that already exists
//    produces the same generic reply as a server hiccup, and so produces the
//    same guidance here — that reply deliberately does not confirm whether
//    the account exists (see api/endpoints/auth.py). Guidance must never
//    become an oracle.
//
// 2. NO RAW TECHNOISE. Status codes and library strings are replaced with
//    plain Arabic. The server's `error_ref` is still surfaced verbatim: it is
//    opaque and is the only way support can correlate a failure.

/**
 * Messages the backend sends, keyed by a recognisable substring.
 *
 * ORDER IS SIGNIFICANT: the first match wins, so a specific rule must sit
 * above a broader one that its message also contains. The lockout rules below
 * are the reason — "…غير صحيحة — بقي لك 3 محاولات" also contains the generic
 * login refusal, and a generic rule reached first would silently strip the
 * countdown the server just computed.
 *
 * A null value means "use the server's own Arabic wording unchanged".
 */
const BY_DETAIL = [
  // --- registration / login (auth.py) ---
  ['اسم المستخدم قصير', 'اسم المستخدم قصير جدًّا — اكتب 3 أحرف على الأقل ثم حاول مجددًا.'],
  ['لا يقبل مسافات', 'اسم المستخدم لا يقبل مسافات — استبدلها بشرطة أو رقم، مثل: ali_01.'],
  ['غير متطابقتين', 'كلمتا المرور غير متطابقتين — أعد كتابة كلمة المرور في الحقلين.'],
  ['كلمة المرور ضعيفة', 'كلمة المرور ضعيفة — استخدم 8 أحرف على الأقل، وتجنّب الأرقام المتتالية (1234) والكلمات الشائعة.'],
  [
    'اسم المستخدم غير متاح',
    'اسم المستخدم غير متاح، يرجى تغييره — جرّب اسمًا آخر.',
  ],
  [
    'تعذّر إنشاء الحساب',
    // The backend now names a taken name explicitly, so this branch is only
    // reached for a genuine fault. It must NOT claim the name was taken:
    // the user's name may be perfectly fine.
    'تعذّر إنشاء الحساب بسبب في الخادم — اسمك قد يكون سليمًا، أعد المحاولة بعد لحظات.',
  ],
  [
    'تم إيقاف الدخول مؤقتًا',
    // null = pass the server's own wording through. It already names the exact
    // next step AND the wait, so restating it here could only drift out of sync
    // with api.auth_lockout.LOCK_MINUTES.
    null,
  ],
  [
    'بقي لك',
    // Same: the server already counted down for the user.
    null,
  ],
  [
    'Too many requests',
    'محاولات كثيرة في وقت قصير — انتظر قليلًا ثم أعد المحاولة.',
  ],
  [
    'غير صحيحة',
    'اسم المستخدم أو كلمة المرور غير صحيحة — تأكد من الاسم (بلا مسافات) ومن لصق كلمة المرور بشكل صحيح.',
  ],

  // --- documents (documents.py) ---
  [
    'not allowed',
    'صيغة الملف غير مدعومة — ارفع PDF أو TXT أو DOCX.',
  ],
  [
    'File too large',
    'حجم الملف أكبر من الحد المسموح — اختر ملفًا أصغر أو قسّم المستند إلى ملفين.',
  ],
  [
    'يرجى الانتظار حتى انتهاء الرفع الحالي',
    'يوجد رفع جارٍ الآن — انتظر حتى ينتهي ثم أعد المحاولة.',
  ],
  [
    'Only failed documents can be retried',
    'إعادة المعالجة متاحة للمستندات الفاشلة فقط — هذا المستند جاهز أو ما زال قيد المعالجة.',
  ],
  [
    'فشلت إعادة معالجة المستند',
    'فشلت إعادة المعالجة — جرّب حذف المستند ورفعه من جديد.',
  ],

  // --- chat (chat.py) ---
  ['Question cannot be empty', 'اكتب سؤالًا أولًا ثم أرسله.'],
  [
    'Question exceeds maximum length',
    'السؤال طويل جدًّا — اعتمد على فكرة واحدة في كل سؤال.',
  ],
  [
    // The account has not supplied a key of its own
    // (api/endpoints/chat.py::_missing_credential_response).
    //
    // This rule earns its place twice over. The status is 401, whose generic
    // entry claims the SESSION expired — true for /auth/* and actively wrong
    // here, since the user is logged in fine and simply has no key. And the
    // rule must sit ABOVE anything broader: the server's own Arabic already
    // names both the state and the fix, so re-wording it here would only
    // drift out of step with core/exceptions.NO_PERSONAL_KEY_DETAIL.
    'لم تُدخل مفتاح',
    null,
  ],
];

/** Fallbacks keyed by HTTP status, for when no message matches. */
const BY_STATUS = {
  400: 'الطلب غير صالح — راجع البيانات المدخلة وحاول مرة أخرى.',
  401: 'انتهت الجلسة — سجّل الدخول من جديد للمتابعة.',
  403: 'لا تملك صلاحية تنفيذ هذا الإجراء.',
  404: 'العنصر المطلوب غير موجود — حدّث الصفحة وحاول مجددًا.',
  409: 'يوجد تعارض مع عملية جارية — انتظر قليلًا ثم أعد المحاولة.',
  413: 'حجم الملف أكبر من الحد المسموح — اختر ملفًا أصغر.',
  422: 'البيانات المدخلة غير صحيحة — راجع الحقول المطلوبة وحاول مجددًا.',
  429: 'محاولات كثيرة في وقت قصير — انتظر قليلًا ثم أعد المحاولة.',
  500: 'حدث خطأ في الخادم — أعد المحاولة بعد لحظات.',
  502: 'الخدمة غير متاحة مؤقتًا — أعد المحاولة بعد قليل.',
  503: 'الخدمة غير متاحة الآن — تأكد من بدء الخادم ثم أعد المحاولة.',
  504: 'استغرق الرد طويلًا — جرّب سؤالًا أقصر أو أعد المحاولة.',
}

/** Network-level failure: the request never reached the server. */
const OFFLINE =
  'تعذّر الاتصال بالخادم — تأكد من تشغيل الخادم ومن اتصالك بالإنترنت ثم أعد المحاولة.'

/**
 * Turn a thrown API error into actionable Arabic guidance.
 *
 * @param {unknown} err  Error from the API client (carries .status/.detail).
 * @param {string} [fallback]  Last-resort message when nothing else matches.
 * @returns {string} A message that names the next step.
 */
export function friendlyError(err, fallback = 'حدث خطأ غير متوقع — أعد المحاولة.') {
  if (!err) return fallback
  const detail = typeof err.detail === 'string' ? err.detail : ''
  const status = Number(err.status) || 0

  // A fetch/network failure has no status at all.
  if (!status && !detail) {
    if (err instanceof TypeError || /network|fetch|load failed/i.test(err.message || '')) {
      return OFFLINE
    }
    return err.message || fallback
  }

  for (const [needle, arabic] of BY_DETAIL) {
    if (detail.toLowerCase().includes(needle.toLowerCase())) {
      // A null entry means the server's own Arabic is already the best
      // wording available — reformatting it would only lose detail.
      return withReference(arabic || detail, detail)
    }
  }

  // The server may send an already-Arabic message we have no rule for
  // (e.g. a new validator). Passing it through beats hiding it.
  if (isArabic(detail)) {
    return withReference(detail, detail)
  }

  return withReference(BY_STATUS[status] || fallback, detail)
}

/**
 * Keep the opaque error_ref so support can correlate, without leaking detail.
 * It is only ever an identifier, never the raw exception.
 */
function withReference(message, detail) {
  const ref = detail.match(/error_ref[:=]\s*([A-Za-z0-9_-]+)/)
  return ref ? `${message} (رقم الخطأ: ${ref[1]})` : message
}

function isArabic(text) {
  return /[؀-ۿ]/.test(text)
}

export default friendlyError