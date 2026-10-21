<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ side: 'left' | 'right' }>()
// Reuse a small architectural vocabulary across both banks and business modes.
const layout = {
  left: [
    { kind: 'townhouse', x: 18, y: 166 }, { kind: 'tower', x: 116, y: 46 },
    { kind: 'hall', x: 218, y: 150 }, { kind: 'terrace', x: 381, y: 142 },
    { kind: 'townhouse', x: 479, y: 166 },
  ],
  right: [
    { kind: 'townhouse', x: 22, y: 166 }, { kind: 'hall', x: 121, y: 150 },
    { kind: 'tower', x: 285, y: 46 }, { kind: 'terrace', x: 390, y: 142 },
    { kind: 'townhouse', x: 493, y: 166 },
  ],
}
const blocks = computed(() => layout[props.side])
function asset(name: string) { return `journey-city-${props.side}-${name}` }
</script>

<template>
  <svg viewBox="0 0 640 336" fill="none" focusable="false">
    <defs>
      <linearGradient :id="asset('stone')" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#e2e5ce" /><stop offset="1" stop-color="#c1d1bc" /></linearGradient>
      <linearGradient :id="asset('mint')" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#c4dccc" /><stop offset="1" stop-color="#a5c5b2" /></linearGradient>
      <linearGradient :id="asset('glass')" x1="0" y1="0" x2="1" y2="1"><stop stop-color="#f3f3db" /><stop offset="1" stop-color="#d6e7d7" /></linearGradient>

      <g :id="asset('townhouse')">
        <path d="M8 137V36H81V137Z" :fill="`url(#${asset('stone')})`" />
        <path d="M81 36L90 42V137H81Z" fill="#b5c8b2" />
        <path d="M1 37L44 5L89 37Z" fill="#94b5a1" /><path d="M8 35L44 10L80 35" stroke="#c5d4b9" stroke-width="2" />
        <path d="M67 23V7H76V30Z" fill="#b1c5af" /><path d="M65 7H78" stroke="#8caa96" stroke-width="2" />
        <circle cx="44" cy="27" r="7" :fill="`url(#${asset('glass')})`" /><path d="M44 21V33M38 27H50" stroke="#a8bfa7" />
        <path d="M11 43H79M11 83H79M7 135H84" stroke="#e8e9d3" stroke-width="3" />
        <g v-for="x in [21, 55]" :key="x">
          <path :d="`M${x} 71V55Q${x + 8} 44 ${x + 16} 55V71Z`" :fill="`url(#${asset('glass')})`" stroke="#a1b99f" />
          <path :d="`M${x + 8} 51V71M${x} 61H${x + 16}M${x - 2} 74H${x + 18}`" stroke="#b0c4aa" stroke-width="1.5" />
        </g>
        <rect x="19" y="94" width="24" height="28" rx="1" fill="#bdd3be" /><path d="M31 94V122M19 108H43" stroke="#eef0d8" stroke-width="1.5" />
        <path d="M54 136V105Q54 94 63 94Q72 94 72 105V136Z" fill="#93b29b" /><path d="M63 100V134" stroke="#c9d7bb" />
        <path d="M14 94L19 86H46L51 94Z" fill="#8eaf97" /><path d="M20 87L16 94M29 87V94M38 87L42 94" stroke="#dae0c3" stroke-width="3" />
        <g class="city-detail" stroke="#b6c7ad" stroke-linecap="round"><path d="M12 51H16M75 79H80M10 115H15M47 127H51M77 105H81" /><path d="M54 138H75M51 141H78" stroke-width="2" /></g>
      </g>

      <g :id="asset('tower')">
        <path d="M10 258V65H22V39H35V16H63V39H76V65H86V258Z" :fill="`url(#${asset('mint')})`" />
        <path d="M86 65L97 73V258H86Z" fill="#90b6a0" />
        <path d="M10 76H86M22 48H76M35 25H63M5 257H99" stroke="#dfE8ce" stroke-width="3" />
        <path d="M48 16V1" stroke="#8aaf98" stroke-width="2" /><path d="M41 16H57" stroke="#8aaf98" stroke-width="2" />
        <g v-for="row in 7" :key="row" :transform="`translate(0 ${76 + row * 22})`">
          <rect v-for="x in [21, 43, 65]" :key="x" :x="x" width="10" height="12" rx="1" :fill="`url(#${asset('glass')})`" />
          <path class="city-detail" d="M19 16H77" stroke="#95b8a1" />
        </g>
        <path d="M33 258V238Q48 223 63 238V258Z" fill="#8eb199" /><path d="M48 231V257M33 244H63" stroke="#c1d4b9" />
        <g class="city-detail" stroke="#cde0c7" stroke-width="2"><path d="M16 82V246M81 82V246M29 54V67M69 54V67M41 32H57" /></g>
      </g>

      <g :id="asset('hall')">
        <path d="M10 153V57H139V153Z" :fill="`url(#${asset('stone')})`" /><path d="M139 57L150 65V153H139Z" fill="#b4c7ad" />
        <path d="M0 58L31 28H116L148 58Z" fill="#9bb8a1" /><path d="M8 58H142M17 65H132" stroke="#e2e4c9" stroke-width="3" />
        <path d="M56 56V18L75 3L94 18V56Z" fill="#c6d4b7" /><path d="M53 18L75 0L97 18Z" fill="#8daa95" />
        <circle cx="75" cy="34" r="12" fill="#eff0d5" stroke="#a3b99f" stroke-width="1.5" /><path d="M75 25V34L81 38" stroke="#8ba58d" stroke-width="1.7" stroke-linecap="round" />
        <g v-for="x in [23, 53, 83, 113]" :key="x">
          <path :d="`M${x} 99V82Q${x + 7} 70 ${x + 14} 82V99Z`" :fill="`url(#${asset('glass')})`" stroke="#a7bba0" />
          <path :d="`M${x + 7} 77V99M${x} 88H${x + 14}`" stroke="#b1c1a5" />
        </g>
        <path d="M17 106H132M17 112H132" stroke="#e6e5cb" stroke-width="2" />
        <path d="M59 153V132Q59 113 75 113Q91 113 91 132V153Z" fill="#9ab59a" /><path d="M75 119V151M61 134H89" stroke="#c4d2b4" stroke-width="1.5" />
        <path d="M25 146V120H42V146M108 146V120H125V146" :fill="`url(#${asset('glass')})`" stroke="#afc1a4" />
        <g class="city-detail" stroke="#b3c3a7"><path d="M18 117V149M48 117V149M102 117V149M132 117V149" stroke-width="2" /><path d="M54 154H96M50 157H100M30 35H49M105 35H119M23 44H49M105 44H127" stroke-width="1.5" /></g>
      </g>

      <g :id="asset('terrace')">
        <path d="M6 161V25H77V161Z" :fill="`url(#${asset('mint')})`" /><path d="M77 25L88 33V161H77Z" fill="#9bbba3" />
        <path d="M0 25H82M10 18H71" stroke="#b4c9b0" stroke-width="4" /><path d="M14 18V11H66V18" stroke="#a4bda3" stroke-width="1.6" />
        <path d="M21 10L24 2L28 10M45 10L50 0L54 10M58 11L63 4L67 11" fill="#a3bf9f" />
        <g v-for="row in 4" :key="row" :transform="`translate(0 ${35 + (row - 1) * 28})`">
          <rect x="17" width="18" height="19" rx="1" :fill="`url(#${asset('glass')})`" /><rect x="48" width="18" height="19" rx="1" :fill="`url(#${asset('glass')})`" />
          <path d="M13 22H70M14 17H69M17 17V22M27 17V22M37 17V22M47 17V22M57 17V22M67 17V22" stroke="#9bb69d" stroke-width="1.2" />
        </g>
        <path d="M21 161V146H37V161M47 161V146H63V161" fill="#94b399" /><path d="M4 161H88" stroke="#dfe5cc" stroke-width="3" />
      </g>
    </defs>

    <g opacity=".5" fill="#d2dfcd">
      <path d="M0 309V231H59V198H112V309M189 309V193H237V166H282V309M342 309V203H411V239H471V309M546 309V212H595V237H640V309" />
      <path d="M72 198V177H99V198M205 193V178H220V193M556 212V191H579V212" />
    </g>
    <use v-for="block in blocks" :key="block.x" :href="`#${asset(block.kind)}`" :transform="`translate(${block.x} ${block.y})`" />

    <path d="M0 308Q144 300 323 307T640 305V336H0Z" fill="#aec8b0" />
    <path d="M0 312Q168 307 328 312T640 310" stroke="#e5e8cd" stroke-width="3" />
    <g class="city-detail" stroke="#94b299" stroke-width="1.6">
      <path d="M20 314V324M45 314V324M70 313V324M95 313V324M120 312V324M145 312V324M480 314V324M505 314V324M530 314V324M555 313V324M580 313V324M605 312V324" />
      <path d="M20 318H145M480 318H605" />
      <path d="M179 307V280Q180 270 190 270H198M447 307V281Q447 271 437 271H430" stroke-width="2.5" />
      <path d="M193 270V277H203V270Z M425 271V278H435V271Z" fill="#f2ebc9" />
    </g>
    <g fill="#9fbe9f"><ellipse cx="161" cy="307" rx="17" ry="7" /><ellipse cx="372" cy="307" rx="20" ry="8" /><ellipse cx="600" cy="305" rx="19" ry="7" /></g>
  </svg>
</template>

<style scoped>
@media (max-width: 1279px) { .city-detail { display: none; } }
</style>
