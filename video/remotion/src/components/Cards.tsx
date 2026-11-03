import React from 'react';
import {Easing, interpolate, useCurrentFrame} from 'remotion';
import {brand} from '../brand';
import type {Receipt} from '../film-data';

const useCardIn = (frames: number) => {
  const f = useCurrentFrame();
  const inP = interpolate(f, [0, 14], [0, 1], {easing: Easing.bezier(0.16, 1, 0.3, 1), extrapolateRight: 'clamp'});
  const outP = interpolate(f, [frames - 10, frames], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return {inP, outP};
};

const Note: React.FC<{children: React.ReactNode}> = ({children}) => (
  <div
    style={{
      fontFamily: brand.font,
      fontSize: 20,
      color: brand.white,
      background: 'rgba(8,22,42,0.92)',
      borderRadius: 10,
      padding: '8px 14px',
      marginTop: 12,
      lineHeight: 1.3,
      display: 'inline-block',
      maxWidth: '100%',
    }}
  >
    {children}
  </div>
);

/** The payout receipt, rebuilt from the page text captured at the moment of the payout (the panel sits below the fold in the recording). */
export const ReceiptCard: React.FC<{receipt: Receipt; frames: number; left?: number; top?: number}> = ({receipt, frames, left = 96, top = 300}) => {
  const {inP, outP} = useCardIn(frames);
  const row = (k: string, v: string, mono = false) => (
    <div style={{display: 'flex', justifyContent: 'space-between', gap: 28, padding: '9px 0', borderTop: '1px solid #e3e8ef'}}>
      <span style={{color: '#5b6b82', fontSize: 24}}>{k}</span>
      <span style={{color: brand.ink, fontWeight: 600, fontSize: 24, fontFamily: mono ? brand.mono : brand.font}}>{v}</span>
    </div>
  );
  return (
    <div
      style={{
        position: 'absolute',
        left,
        top,
        width: 640,
        opacity: Math.min(inP, outP),
        translate: `${(1 - inP) * -50}px 0`,
        fontFamily: brand.font,
      }}
    >
      <div style={{background: '#fff', borderRadius: 18, padding: '22px 28px 20px', boxShadow: '0 18px 50px rgba(0,0,0,.45)', border: '1px solid #d7dfea'}}>
        <div style={{display: 'flex', justifyContent: 'space-between', alignItems: 'center'}}>
          <div style={{fontSize: 22, fontWeight: 600, letterSpacing: 0.8, color: '#5b6b82'}}>{receipt.title}</div>
          <div style={{fontSize: 22, fontWeight: 800, color: '#8a5a00', background: '#fdebc4', borderRadius: 999, padding: '3px 16px'}}>{receipt.status}</div>
        </div>
        <div style={{fontSize: 64, fontWeight: 800, color: brand.ink, margin: '6px 0 10px', letterSpacing: -1}}>{receipt.amount}</div>
        {row('Recipient', receipt.recipient)}
        {row('Payout item', receipt.item, true)}
        {row('Batch', receipt.batch, true)}
        {row('Paid at', receipt.paid_at)}
      </div>
      <Note>PayPal Payouts ids as captured; the receipt panel is below the fold in the recording.</Note>
    </div>
  );
};

/** Agent trace rows, rebuilt from the captured trace text (the trace panel is below the fold in the recording). */
export const TraceCard: React.FC<{ai: string; collect: string; tools: string[]; frames: number; left?: number; top?: number}> = ({ai, collect, tools, frames, left = 40, top = 640}) => {
  const {inP, outP} = useCardIn(frames);
  const f = useCurrentFrame();
  const rowA = (i: number) => interpolate(f, [16 + i * 6, 24 + i * 6], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const aiShort = ai.split('. Gemini')[0];
  const clean = (s: string) => s.replace(/, round \d+$/, '');
  return (
    <div
      style={{
        position: 'absolute',
        left,
        top,
        width: 1520,
        opacity: Math.min(inP, outP),
        translate: `0 ${(1 - inP) * 40}px`,
        fontFamily: brand.font,
      }}
    >
      <div style={{background: 'rgba(8,22,42,0.96)', borderRadius: 16, padding: '14px 24px 12px', boxShadow: '0 18px 50px rgba(0,0,0,.5)', border: '1.5px solid rgba(255,255,255,0.14)'}}>
        <div style={{display: 'flex', gap: 14, alignItems: 'center', marginBottom: 6}}>
          <div style={{fontSize: 20, fontWeight: 800, letterSpacing: 1.2, color: brand.accent}}>TREASURER TRACE</div>
          <div style={{fontSize: 18, fontWeight: 800, color: brand.navy, background: brand.accent, borderRadius: 999, padding: '1px 12px'}}>RULES MODE</div>
          <div style={{fontSize: 18, color: brand.mute}}>{aiShort}</div>
          <div style={{marginLeft: 'auto', fontSize: 16, color: brand.mute, fontStyle: 'italic'}}>from the page text; panel is below the fold in the recording</div>
        </div>
        <div style={{fontFamily: brand.mono, fontSize: 18, color: '#c8d7ec', marginBottom: 4}}>
          <span style={{color: brand.blueSoft}}>TOOL </span>
          {collect}
        </div>
        <div style={{display: 'grid', gridTemplateColumns: '1fr 1fr', columnGap: 28, rowGap: 2}}>
          {tools.map((l, i) => (
            <div key={i} style={{fontFamily: brand.mono, fontSize: 18, color: brand.white, opacity: rowA(i), lineHeight: 1.4}}>
              <span style={{color: brand.good}}>✓ </span>
              {clean(l)}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};

/** What PayPal's own sandbox API returned when the payout batch was read back (GET /v1/payments/payouts/{batch}). */
export const ReadbackCard: React.FC<{rb: {request: string; host: string; payout_batch_id: string; batch_status: string; amount: string; fees: string; currency: string; time_completed: string; payout_item_id: string; transaction_status: string}; left: number; top: number; width?: number}> = ({rb, left, top, width = 820}) => {
  const f = useCurrentFrame();
  const inP = interpolate(f, [0, 14], [0, 1], {easing: Easing.bezier(0.16, 1, 0.3, 1), extrapolateRight: 'clamp'});
  const row = (k: string, v: string, good = false) => (
    <div style={{display: 'flex', justifyContent: 'space-between', gap: 24, padding: '9px 0', borderTop: '1px solid rgba(255,255,255,0.14)', fontSize: 28}}>
      <span style={{color: brand.mute, fontFamily: brand.mono}}>{k}</span>
      <span style={{color: good ? brand.good : brand.white, fontWeight: 700, fontFamily: brand.mono}}>{v}</span>
    </div>
  );
  return (
    <div style={{position: 'absolute', left, top, width, opacity: inP, translate: `${(1 - inP) * 50}px 0`, fontFamily: brand.font, color: brand.white}}>
      <div style={{background: brand.navy, border: `3px solid ${brand.blueSoft}`, borderRadius: 22, padding: '22px 30px 18px', boxShadow: '0 20px 56px rgba(0,0,0,.5)'}}>
        <div style={{fontSize: 24, fontWeight: 800, letterSpacing: 1.2, color: brand.blueSoft}}>READ BACK FROM PAYPAL&apos;S SANDBOX API</div>
        <div style={{fontFamily: brand.mono, fontSize: 21, color: brand.mute, margin: '6px 0 12px', wordBreak: 'break-all'}}>
          {rb.request} · {rb.host}
        </div>
        {row('batch_status', rb.batch_status, true)}
        {row('payout_batch_id', rb.payout_batch_id)}
        {row('payout_item_id', rb.payout_item_id)}
        {row('transaction_status', rb.transaction_status, true)}
        {row('amount', `${rb.amount} ${rb.currency}  (fee ${rb.fees})`)}
        {row('time_completed', rb.time_completed)}
      </div>
    </div>
  );
};
