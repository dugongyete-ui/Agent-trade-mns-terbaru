<template>
    <svg :width="size" :height="size" viewBox="0 0 19 18" fill="none" xmlns="http://www.w3.org/2000/svg"
        :style="{ minWidth: `${size}px`, minHeight: `${size}px` }">
        <g filter="url(#filter0_ii_mcp)">
            <rect x="1.5" y="1.5" width="15" height="15" rx="7.5" fill="url(#paint0_linear_mcp)" />
        </g>
        <rect x="1.92857" y="1.92857" width="14.1429" height="14.1429" rx="7.07143" stroke="#B9B9B7"
            stroke-width="0.857143" />
        <!-- Center node -->
        <circle cx="9" cy="9" r="1.4" stroke="#535350" stroke-width="1.1" />
        <!-- Top node -->
        <circle cx="9" cy="4.5" r="1" stroke="#ACACAC" stroke-width="1" />
        <!-- Bottom-left node -->
        <circle cx="5.8" cy="12" r="1" stroke="#ACACAC" stroke-width="1" />
        <!-- Bottom-right node -->
        <circle cx="12.2" cy="12" r="1" stroke="#ACACAC" stroke-width="1" />
        <!-- Connection lines -->
        <path d="M9 5.5V7.6" stroke="#ACACAC" stroke-width="0.9" stroke-linecap="round" />
        <path d="M6.6 11.4L8.2 10.0" stroke="#ACACAC" stroke-width="0.9" stroke-linecap="round" />
        <path d="M11.4 11.4L9.8 10.0" stroke="#ACACAC" stroke-width="0.9" stroke-linecap="round" />
        <defs>
            <filter id="filter0_ii_mcp" x="1" y="1" width="16" height="16" filterUnits="userSpaceOnUse"
                color-interpolation-filters="sRGB">
                <feFlood flood-opacity="0" result="BackgroundImageFix" />
                <feBlend mode="normal" in="SourceGraphic" in2="BackgroundImageFix" result="shape" />
                <feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0"
                    result="hardAlpha" />
                <feOffset dx="1" dy="1" />
                <feGaussianBlur stdDeviation="0.25" />
                <feComposite in2="hardAlpha" operator="arithmetic" k2="-1" k3="1" />
                <feColorMatrix type="matrix" values="0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 0.6 0" />
                <feBlend mode="normal" in2="shape" result="effect1_innerShadow_mcp" />
                <feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0"
                    result="hardAlpha" />
                <feOffset dx="-1" dy="-1" />
                <feGaussianBlur stdDeviation="0.25" />
                <feComposite in2="hardAlpha" operator="arithmetic" k2="-1" k3="1" />
                <feColorMatrix type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0.08 0" />
                <feBlend mode="normal" in2="effect1_innerShadow_mcp" result="effect2_innerShadow_mcp" />
            </filter>
            <linearGradient id="paint0_linear_mcp" x1="9" y1="1.5" x2="9" y2="16.5" gradientUnits="userSpaceOnUse">
                <stop stop-color="white" stop-opacity="0" />
                <stop offset="1" stop-opacity="0.16" />
            </linearGradient>
        </defs>
    </svg>
</template>

<script setup lang="ts">
defineProps({
    size: { type: Number, default: 21 }
});
</script>
