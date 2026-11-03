/** Member Passbook helpers: words in English and Hindi, and the token from the URL fragment. Pure. */
import type { PassbookView } from "./types";

export type Lang = "en" | "hi";

/** The capability lives in the fragment (`/m#tkc1...`): browsers never send it to the server. */
export function tokenFromHash(hash: string): string | null {
  const t = (hash || "").replace(/^#/, "").trim();
  return t.startsWith("tkc1.") && t.length < 2048 ? t : null;
}

const T = {
  en: {
    demo: "Demo member view, no login",
    payments: "My payments",
    thisRound: "This round",
    myTurn: "My turn",
    fees: "My fees",
    misses: "If someone misses",
    repay: "My repayment",
    receipt: "Receipt: who allowed this charge",
    replay: "replay, no PayPal call",
    replayNote: "This row is from the demo's replay: no money moved and PayPal was not called.",
    paidOf: (p: number, t: number) => `${p} of ${t} paid`,
    covered: (n: number) => `${n} covered by the circle buffer`,
    youCovered: "Your share was covered by the circle buffer. Nobody was chased.",
    turnMoved: (a: number, b: number) => `Round ${a} → ${b} (a swap you agreed to)`,
    turnAt: (n: number) => `You receive the pot in round ${n}`,
    expected: "Expected pot",
    feesPaid: "Fees paid so far",
    perPayment: "PayPal fee per payment",
    buffer: "Organizer buffer",
    menu: "What the charter allows",
    quorum: "Votes needed",
    grace: "Grace days",
    hash: "Charter hash",
    payNow: "Scan to pay on PayPal",
    expired: "This link has expired or is not valid. Ask the organizer for a new one.",
    off: "The member passbook is not switched on for this demo.",
    status: { COMPLETED: "paid", COVERED: "covered by the buffer", DECLINED: "declined" } as Record<string, string>,
  },
  hi: {
    demo: "डेमो सदस्य दृश्य, लॉगिन नहीं",
    payments: "मेरे भुगतान",
    thisRound: "यह राउंड",
    myTurn: "मेरी बारी",
    fees: "मेरी फ़ीस",
    misses: "अगर कोई चूक जाए",
    repay: "मेरी वापसी",
    receipt: "रसीद: यह भुगतान किसने मंज़ूर किया",
    replay: "रीप्ले, कोई PayPal कॉल नहीं",
    replayNote: "यह पंक्ति डेमो के रीप्ले से है: कोई पैसा नहीं गया और PayPal को कॉल नहीं किया गया।",
    paidOf: (p: number, t: number) => `${t} में से ${p} ने भुगतान किया`,
    covered: (n: number) => `${n} का हिस्सा सर्कल बफ़र से भरा गया`,
    youCovered: "आपका हिस्सा सर्कल बफ़र से भरा गया। किसी ने तक़ाज़ा नहीं किया।",
    turnMoved: (a: number, b: number) => `राउंड ${a} → ${b} (आपकी सहमति से अदला-बदली)`,
    turnAt: (n: number) => `आपको राउंड ${n} में पॉट मिलेगा`,
    expected: "अपेक्षित पॉट",
    feesPaid: "अब तक दी गई फ़ीस",
    perPayment: "हर भुगतान पर PayPal फ़ीस",
    buffer: "आयोजक का बफ़र",
    menu: "चार्टर क्या अनुमति देता है",
    quorum: "ज़रूरी वोट",
    grace: "छूट के दिन",
    hash: "चार्टर हैश",
    payNow: "PayPal पर भुगतान के लिए स्कैन करें",
    expired: "यह लिंक समाप्त हो गया है या मान्य नहीं है। आयोजक से नया लिंक माँगें।",
    off: "इस डेमो के लिए सदस्य पासबुक चालू नहीं है।",
    status: { COMPLETED: "भुगतान हुआ", COVERED: "बफ़र से भरा गया", DECLINED: "अस्वीकृत" } as Record<string, string>,
  },
};

export function words(lang: Lang) {
  return T[lang];
}

export const REMEDY_WORDS: Record<Lang, Record<string, string>> = {
  en: {
    retry_on_date: "retry on your payday",
    turn_swap: "swap turns (only with consent)",
    buffer_advance: "the buffer covers the share",
    repay_by_invoice: "repay by PayPal invoice",
  },
  hi: {
    retry_on_date: "वेतन के दिन दोबारा प्रयास",
    turn_swap: "बारी की अदला-बदली (सहमति से ही)",
    buffer_advance: "बफ़र हिस्सा भर देता है",
    repay_by_invoice: "PayPal इनवॉइस से वापसी",
  },
};

/** The round line. Dignity rule: counts only; "you" only for the viewer's own covered share. */
export function roundLine(view: PassbookView, lang: Lang): string[] {
  const r = view.this_round;
  if (!r) return [];
  const w = words(lang);
  const out = [w.paidOf(r.paid, r.total)];
  if (r.covered_by_buffer > 0) out.push(w.covered(r.covered_by_buffer));
  if (r.mine === "COVERED") out.push(w.youCovered);
  return out;
}

export function turnLine(view: PassbookView, lang: Lang): string | null {
  const t = view.turn;
  const w = words(lang);
  if (t.moved && t.original_round && t.round_n) return w.turnMoved(t.original_round, t.round_n);
  return t.round_n ? w.turnAt(t.round_n) : null;
}
