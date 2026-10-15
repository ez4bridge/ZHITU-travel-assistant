<template>
  <div class="city-scene" aria-hidden="true">
    <svg viewBox="0 0 620 430" fill="none" xmlns="http://www.w3.org/2000/svg">
      <defs>
        <linearGradient id="sky" x1="310" y1="0" x2="310" y2="430" gradientUnits="userSpaceOnUse"><stop stop-color="#e6f1e9"/><stop offset="1" stop-color="#eff2dd"/></linearGradient>
        <linearGradient id="river" x1="310" y1="302" x2="310" y2="430" gradientUnits="userSpaceOnUse"><stop stop-color="#91c0b8"/><stop offset="1" stop-color="#c5ded2"/></linearGradient>
        <pattern id="windows" width="16" height="20" patternUnits="userSpaceOnUse"><rect x="4" y="5" width="5" height="9" rx="1" fill="#e0efe1" opacity=".72"/></pattern>
      </defs>
      <rect width="620" height="430" rx="28" fill="url(#sky)"/>
      <circle cx="459" cy="98" r="40" fill="#f3cc85"/>
      <path d="M-20 214Q65 152 148 202Q234 150 320 207Q420 151 503 207Q571 164 649 210V325H-20Z" fill="#ceddd0"/>
      <path d="M42 297V229H87V257H109V205H149V298" fill="#9ebfb0"/>
      <path d="M112 303V235H145V205H191V236H210V310" fill="#71a69a"/>
      <rect x="148" y="222" width="43" height="84" fill="url(#windows)"/>
      <path d="M210 305V158H254V119L270 102L286 119V158H326V309" fill="#468b7d"/>
      <path d="M222 167H314V302H222Z" fill="url(#windows)"/>
      <path d="M272 107V79" stroke="#468b7d" stroke-width="4"/>
      <path d="M375 302L388 198L399 127L412 198L425 302" fill="#83a899"/>
      <path d="M399 58V293" stroke="#427e73" stroke-width="5"/>
      <ellipse cx="399" cy="137" rx="18" ry="23" fill="#cf8a6c"/>
      <circle cx="399" cy="199" r="34" fill="#e0a281"/>
      <path d="M386 171C370 189 370 208 386 226" stroke="#f5d2af" stroke-width="4"/>
      <path d="M450 306V226H473V195H513V226H542V306" fill="#aac4aa"/>
      <path d="M479 202H506V301H479Z" fill="url(#windows)"/>
      <path d="M0 287Q139 270 303 293T620 279V330H0Z" fill="#608f76"/>
      <path d="M0 309Q150 296 311 317T620 302V430H0Z" fill="url(#river)"/>
      <path d="M25 343Q199 326 365 348T601 334M93 372Q275 357 444 378M-19 397Q148 375 288 398" stroke="#e8f2dc" stroke-width="3" stroke-linecap="round" opacity=".8"/>
      <path d="M64 294Q187 271 318 293" stroke="#eee9ce" stroke-width="5" stroke-linecap="round"/>
      <path d="M87 293V316M116 287V311M145 284V309M174 283V308M203 283V309M232 285V311M261 289V313M291 293V317" stroke="#eee9ce" stroke-width="3"/>
      <path d="M465 359H505L497 368H471Z" fill="#e9eee1"/>
      <path d="M476 358V345H492V358" fill="#d79f76"/>
      <path d="M56 132Q67 122 78 132M86 117Q95 109 104 117" stroke="#739d8d" stroke-width="2.5" stroke-linecap="round"/>
      <path d="M551 270V309M574 281V312" stroke="#466e57" stroke-width="5"/>
      <circle cx="551" cy="256" r="21" fill="#8da56c"/><circle cx="574" cy="271" r="17" fill="#a3b17d"/>
    </svg>
    <div class="scene-tag"><span class="scene-tag-dot"></span> 城市漫游，慢一点也很好</div>
    <div class="scene-caption"><span>下一站，属于你的风景</span><strong>让好奇心带路。</strong></div>
  </div>
</template>
