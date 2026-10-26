// Inter and Noto Sans Devanagari are bundled locally (public/fonts; OFL-1.1) - no CDN at render time.
import {loadFont} from '@remotion/fonts';
import {staticFile} from 'remotion';

const weights = ['400', '600', '800'] as const;

export const fontsReady = Promise.all([
  ...weights.map((weight) =>
    loadFont({
      family: 'Inter',
      url: staticFile(`fonts/inter-latin-${weight}-normal.woff2`),
      weight,
      format: 'woff2',
    }),
  ),
  ...(['400', '600'] as const).map((weight) =>
    loadFont({
      family: 'Noto Sans Devanagari',
      url: staticFile(`fonts/noto-deva-${weight}.woff2`),
      weight,
      format: 'woff2',
    }),
  ),
]);
