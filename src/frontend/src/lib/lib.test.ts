import fc from 'fast-check';
import { describe, expect, it } from 'vitest';
import type { RoutineItem } from '../data/sampleRoutine';
import { downsample, floatToPcm16, pcm16ToFloat } from '../voice/pcm';
import { PlaybackQueue } from '../voice/playbackQueue';
import { buildBearerCheck } from './testHelpers';
import { BEARER_PREFIX, bearerProtocols, decodeBearerSubprotocol, encodeBearerSubprotocol } from './bearer';
import { formatMXN, formatPrice, parsePrice, truncate } from './format';
import { columnsForWidth } from './layout';
import { buildQrUrl, parseQrUrl } from './qr';
import { productCardViewModel, readingsViewModel } from './viewModels';

const RUNS = { numRuns: 100 };
const uuidV4 = fc.uuid({ version: 4 });

// ---- Feature: skincare-voice-advisor, Property 14: la conversión a PCM16 preserva el audio dentro de la cuantización ----

describe('PCM16', () => {
  it('ida y vuelta: el error es menor que un paso de cuantización', () => {
    fc.assert(
      fc.property(fc.array(fc.float({ min: -1, max: 1, noNaN: true }), { maxLength: 300 }), (values) => {
        const original = Float32Array.from(values);
        const back = pcm16ToFloat(floatToPcm16(original));
        expect(back.length).toBe(original.length);
        for (let i = 0; i < original.length; i++) {
          expect(Math.abs(back[i]! - original[i]!)).toBeLessThanOrEqual(1 / 0x7fff + 1e-7);
        }
      }),
      RUNS,
    );
  });

  it('recorta lo que sale de [-1, 1] en lugar de desbordar', () => {
    expect(Array.from(floatToPcm16(Float32Array.from([2, -2])))).toEqual([0x7fff, -0x8000]);
  });

  it('downsample conserva la forma de una señal lenta y nunca aumenta la frecuencia', () => {
    const tone = Float32Array.from({ length: 4800 }, (_, i) => Math.sin((2 * Math.PI * 200 * i) / 48000));
    const out = downsample(tone, 48000, 16000);
    expect(out.length).toBeGreaterThanOrEqual(1599);
    expect(out.length).toBeLessThanOrEqual(1600);
    for (let i = 0; i < out.length; i++) {
      expect(Math.abs(out[i]! - Math.sin((2 * Math.PI * 200 * i * 3) / 48000))).toBeLessThan(0.02);
    }
    expect(downsample(tone, 16000, 48000)).toEqual(tone);
    expect(downsample(new Float32Array(0), 48000, 16000).length).toBe(0);
  });
});

// ---- Feature: skincare-voice-advisor, Property 15: una interrupción vacía la cola de reproducción -----------------------

describe('PlaybackQueue', () => {
  it('después de clear() no suena nada de lo anterior', () => {
    fc.assert(
      fc.property(fc.array(fc.array(fc.float({ noNaN: true }), { minLength: 1, maxLength: 50 }), { maxLength: 10 }), (chunks) => {
        const queue = new PlaybackQueue();
        chunks.forEach((c) => queue.push(Float32Array.from(c)));
        queue.pull(7); // algo ya empezó a sonar
        queue.clear();
        expect(queue.pending).toBe(0);
        expect(queue.playing).toBe(false);
        expect(Array.from(queue.pull(64)).every((v) => v === 0)).toBe(true);
      }),
      RUNS,
    );
  });

  it('entrega el audio en orden y completa con silencio', () => {
    const queue = new PlaybackQueue();
    queue.push(Float32Array.from([1, 2, 3]));
    queue.push(Float32Array.from([4, 5]));
    expect(Array.from(queue.pull(4))).toEqual([1, 2, 3, 4]);
    expect(Array.from(queue.pull(4))).toEqual([5, 0, 0, 0]);
    expect(queue.playing).toBe(false);
  });

  it('el audio que llega después de un clear() sí se reproduce', () => {
    const queue = new PlaybackQueue();
    queue.push(Float32Array.from([9, 9]));
    queue.clear();
    queue.push(Float32Array.from([1]));
    expect(Array.from(queue.pull(2))).toEqual([1, 0]);
  });
});

// ---- Feature: skincare-voice-advisor, Property 49: la codificación del JWT en el subprotocolo hace ida y vuelta --------

describe('subprotocolo con el JWT', () => {
  it('ida y vuelta para cualquier texto y solo con caracteres base64url', () => {
    fc.assert(
      fc.property(fc.string({ unit: 'binary', maxLength: 200 }), (token) => {
        const protocol = encodeBearerSubprotocol(token);
        expect(protocol.startsWith(BEARER_PREFIX)).toBe(true);
        expect(buildBearerCheck(protocol.slice(BEARER_PREFIX.length))).toBe(true);
        expect(decodeBearerSubprotocol(protocol)).toBe(token);
      }),
      RUNS,
    );
  });

  it('un JWT real (tres partes con puntos y guiones) se recupera igual', () => {
    const jwt = 'eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJrLTEiLCJncm91cHMiOlsia2lvc2NvIl19.c2lnLV8';
    expect(decodeBearerSubprotocol(encodeBearerSubprotocol(jwt))).toBe(jwt);
    expect(bearerProtocols(jwt)).toEqual([encodeBearerSubprotocol(jwt), 'base64UrlBearerAuthorization']);
  });

  it.each(['', 'otro.abc', `${BEARER_PREFIX}@@@`, `${BEARER_PREFIX}a`])('rechaza %j', (bad) => {
    expect(decodeBearerSubprotocol(bad)).toBeNull();
  });
});

// ---- Feature: skincare-voice-advisor, Property 35: la URL del QR hace ida y vuelta -------------------------------------

describe('URL del QR', () => {
  it('buildQrUrl → parseQrUrl devuelve el mismo rec_id', () => {
    fc.assert(
      fc.property(uuidV4, fc.domain(), (recId, domain) => {
        const url = buildQrUrl(domain, recId);
        expect(parseQrUrl(url)).toBe(recId);
        expect(parseQrUrl(url, domain)).toBe(recId);
      }),
      RUNS,
    );
  });

  it('acepta el dominio con esquema y barra final, y rechaza otros dominios o ids que no son UUID v4', () => {
    const id = '3f0c6a0e-6f0f-4d0e-9d6b-8f1f3e2a9c11';
    expect(buildQrUrl('https://tienda.example/', id)).toBe(`https://tienda.example/caja?rec=${id}`);
    expect(parseQrUrl(buildQrUrl('tienda.example', id), 'otra.example')).toBeNull();
    expect(parseQrUrl('https://tienda.example/caja?rec=123')).toBeNull();
    expect(parseQrUrl('https://tienda.example/otra?rec=' + id)).toBeNull();
  });
});

// ---- Feature: skincare-voice-advisor, Property 39: el formato MXN hace ida y vuelta -------------------------------------

describe('formato de precio', () => {
  it('formatPrice → parsePrice devuelve los mismos centavos', () => {
    fc.assert(
      fc.property(fc.integer({ min: 0, max: 99_999_999 }), (cents) => {
        expect(parsePrice(formatPrice(cents))).toBe(cents);
      }),
      RUNS,
    );
  });

  it('separador de miles con coma y siempre dos decimales', () => {
    expect(formatMXN(123450)).toBe('$1,234.50');
    expect(formatMXN(100)).toBe('$1.00');
    expect(formatMXN(123456789)).toBe('$1,234,567.89');
    expect(formatMXN(5)).toBe('$0.05');
  });

  it.each(['1234.50', '$1,23.00', '$12.5', 'abc', ''])('parsePrice rechaza %j', (bad) => {
    expect(parsePrice(bad)).toBeNull();
  });

  it('truncate nunca pasa del máximo y no toca lo que cabe', () => {
    fc.assert(
      fc.property(fc.string({ maxLength: 300 }), fc.integer({ min: 1, max: 200 }), (text, max) => {
        const out = truncate(text, max);
        expect(out.length).toBeLessThanOrEqual(Math.max(max, 1));
        if (text.length <= max) expect(out).toBe(text);
      }),
      RUNS,
    );
  });
});

// ---- Feature: skincare-voice-advisor, Property 44: la tarjeta de lecturas muestra los primeros 5 títulos en orden ---------

describe('tarjeta de lecturas', () => {
  it('muestra a lo más 5 títulos, en orden, cada uno de hasta 150 caracteres', () => {
    fc.assert(
      fc.property(fc.array(fc.string({ maxLength: 400 }), { maxLength: 12 }), (titles) => {
        const shown = readingsViewModel(titles.map((titulo) => ({ titulo })));
        expect(shown).toHaveLength(Math.min(5, titles.length));
        shown.forEach((title, i) => {
          expect(title.length).toBeLessThanOrEqual(150);
          expect(titles[i]!.startsWith(title.replace(/…$/, '').trimEnd().slice(0, 149))).toBe(true);
        });
      }),
      RUNS,
    );
  });
});

// ---- Feature: skincare-voice-advisor, Property 47: la tarjeta de producto muestra todos los campos y trunca la razón -----

describe('tarjeta de producto', () => {
  const item = fc.record({
    paso: fc.constantFrom<RoutineItem['paso']>(1, 2, 3, 4),
    sku: fc.stringMatching(/^[0-9]{9}$/),
    nombre: fc.string({ minLength: 1, maxLength: 60 }),
    marca: fc.string({ minLength: 1, maxLength: 30 }),
    precioCents: fc.integer({ min: 0, max: 99_999_999 }),
    imagenUrl: fc.constant(''),
    razonCatalogo: fc.string({ maxLength: 400 }),
    modoUso: fc.string({ maxLength: 200 }),
  });

  it('conserva todos los campos, precio con miles y dos decimales, razón de hasta 120 caracteres', () => {
    fc.assert(
      fc.property(item, (p) => {
        const view = productCardViewModel(p);
        expect(view).toMatchObject({ paso: p.paso, sku: p.sku, nombre: p.nombre, marca: p.marca, modoUso: p.modoUso });
        expect(view.precio).toBe(formatPrice(p.precioCents));
        expect(view.precio).toMatch(/^\$\d{1,3}(,\d{3})*\.\d{2}$/);
        expect(view.razon.length).toBeLessThanOrEqual(120);
        if (p.razonCatalogo.length <= 120) expect(view.razon).toBe(p.razonCatalogo);
        else expect(view.razon.endsWith('…')).toBe(true);
      }),
      RUNS,
    );
  });
});

// ---- columnas de la rutina ---------------------------------------------------------------------------------------------

describe('columnsForWidth', () => {
  it.each([
    [1600, 4], [1024, 4], [1023, 2], [600, 2], [599, 1], [320, 1],
  ])('ancho %i → %i columnas', (width, columns) => {
    expect(columnsForWidth(width)).toBe(columns);
  });

  it('es monótona: a más ancho, nunca menos columnas', () => {
    fc.assert(
      fc.property(fc.integer({ min: 200, max: 2500 }), fc.integer({ min: 0, max: 500 }), (w, extra) => {
        expect(columnsForWidth(w + extra)).toBeGreaterThanOrEqual(columnsForWidth(w));
      }),
      RUNS,
    );
  });
});
