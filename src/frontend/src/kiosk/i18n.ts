export type Language = 'es' | 'en';

export interface KioskStrings {
  brand: string;
  brandSub: string;
  // Inicio
  voiceAdvice: string;
  idleTitle: string;
  idleIntro: string;
  startCta: string;
  anonNotice: string;
  howItWorks: string;
  steps: { title: string; body: string }[];
  // Conversación
  inProgress: string;
  languageName: string;
  listening: string;
  speakNaturally: string;
  mute: string;
  unmute: string;
  hangup: string;
  hangupNote: string;
  demoLink: string;
  transcript: string;
  advisor: string;
  you: string;
  noisy: string;
  typePlaceholder: string;
  send: string;
  voiceEnded: string;
  // Rutina
  advisorListening: string;
  yourRoutine: string;
  oneProductPerStep: string;
  stepNames: [string, string, string, string];
  sku: string;
  howToUse: string;
  close: string;
  forCheckout: string;
  code: string;
  takePhoto: string;
  optionalReadings: string;
  readingsSource: string;
  routineNote: string;
  imagePlaceholder: string;
  imageAlt: (name: string) => string;
  noProductForStep: string;
}

const es: KioskStrings = {
  brand: 'GRUPO ULTRA',
  brandSub: 'ASESOR DE SKINCARE',
  voiceAdvice: 'Asesoría por voz',
  idleTitle: 'Cuéntanos qué busca tu piel.',
  idleIntro:
    'Conversa con nuestro asesor y recibe una rutina con productos de la tienda, lista para llevar a caja.',
  startCta: 'Iniciar conversación',
  anonNotice:
    'La conversación es anónima: no pedimos tu nombre. [AVISO DE USO DE DATOS DE GRUPO ULTRA]',
  howItWorks: 'Cómo funciona',
  steps: [
    {
      title: 'Toca «Iniciar conversación»',
      body: 'Permite el uso del micrófono cuando la tablet lo pida.',
    },
    {
      title: 'Habla con el asesor',
      body: 'En español o en inglés. Te hará unas preguntas sobre tu piel.',
    },
    {
      title: 'Recibe tu rutina y un código QR',
      body: 'Aparecen en esta pantalla, con un producto por cada paso.',
    },
    {
      title: 'Toma foto del QR y llévala a caja',
      body: 'Al terminar toca «Colgar» para cerrar la conversación.',
    },
  ],
  inProgress: 'Conversación en curso',
  languageName: 'Español',
  listening: 'Te estamos escuchando',
  speakNaturally: 'Habla con naturalidad, como con un asesor en tienda.',
  mute: 'Silenciar',
  unmute: 'Activar micrófono',
  hangup: 'Colgar',
  hangupNote: 'Al colgar se cierra la conversación y la pantalla vuelve al inicio.',
  demoLink: 'Demo: ver rutina →',
  transcript: 'Transcripción',
  advisor: 'Asesor',
  you: 'Tú',
  noisy: '¿Hay mucho ruido? También puedes escribir',
  typePlaceholder: 'Escribe tu mensaje',
  send: 'Enviar',
  voiceEnded: 'La conversación por voz terminó',
  advisorListening: 'El asesor sigue escuchando',
  yourRoutine: 'Tu rutina',
  oneProductPerStep: 'Un producto del catálogo por cada paso',
  stepNames: ['Limpieza', 'Tratamiento', 'Hidratación', 'Protección solar'],
  sku: 'SKU',
  howToUse: 'Ver modo de uso',
  close: 'Cerrar',
  forCheckout: 'Para la caja',
  code: 'Código',
  takePhoto: 'Toma una foto de este código y muéstrala en caja.',
  optionalReadings: 'Opcional · Lecturas sobre',
  readingsSource: 'Fuente: PubMed (NCBI). Información general, no es consejo médico.',
  routineNote:
    'Si quieres saber cómo combinar los productos, consulta a un asesor de la tienda. Puedes seguir preguntando por voz antes de colgar.',
  imagePlaceholder: 'Imagen del producto (columna «Imagen» del CSV)',
  imageAlt: (name) => `Imagen de ${name}`,
  noProductForStep: 'No hay producto disponible para este paso.',
};

const en: KioskStrings = {
  brand: 'GRUPO ULTRA',
  brandSub: 'SKINCARE ADVISOR',
  voiceAdvice: 'Voice advice',
  idleTitle: 'Tell us what your skin is looking for.',
  idleIntro:
    'Talk with our advisor and get a routine made of store products, ready to take to the register.',
  startCta: 'Start conversation',
  anonNotice:
    "The conversation is anonymous: we don't ask for your name. [GRUPO ULTRA DATA USE NOTICE]",
  howItWorks: 'How it works',
  steps: [
    {
      title: 'Tap «Start conversation»',
      body: 'Allow microphone access when the tablet asks for it.',
    },
    {
      title: 'Talk to the advisor',
      body: 'In Spanish or English. They will ask a few questions about your skin.',
    },
    {
      title: 'Get your routine and a QR code',
      body: 'They appear on this screen, with one product for each step.',
    },
    {
      title: 'Take a photo of the QR and bring it to the register',
      body: 'When you are done, tap «Hang up» to end the conversation.',
    },
  ],
  inProgress: 'Conversation in progress',
  languageName: 'English',
  listening: "We're listening",
  speakNaturally: 'Speak naturally, as you would with an in-store advisor.',
  mute: 'Mute',
  unmute: 'Unmute',
  hangup: 'Hang up',
  hangupNote: 'Hanging up ends the conversation and returns to the start screen.',
  demoLink: 'Demo: see routine →',
  transcript: 'Transcript',
  advisor: 'Advisor',
  you: 'You',
  noisy: 'Too noisy? You can also type',
  typePlaceholder: 'Type your message',
  send: 'Send',
  voiceEnded: 'The voice conversation has ended',
  advisorListening: 'The advisor is still listening',
  yourRoutine: 'Your routine',
  oneProductPerStep: 'One catalog product for each step',
  stepNames: ['Cleanser', 'Treatment', 'Moisturizer', 'Sun protection'],
  sku: 'SKU',
  howToUse: 'How to use',
  close: 'Close',
  forCheckout: 'For the register',
  code: 'Code',
  takePhoto: 'Take a photo of this code and show it at the register.',
  optionalReadings: 'Optional · Readings about',
  readingsSource: 'Source: PubMed (NCBI). General information, not medical advice.',
  routineNote:
    'If you want to know how to combine the products, ask a store advisor. You can keep asking by voice before hanging up.',
  imagePlaceholder: 'Product image (CSV «Image» column)',
  imageAlt: (name) => `Image of ${name}`,
  noProductForStep: 'No product available for this step.',
};

export const STRINGS: Record<Language, KioskStrings> = { es, en };
