name	description	metadata
anti-slop-design
Create distinctive, production-grade frontend interfaces that avoid generic AI aesthetics. Use when building web components, game UIs, landing pages, dashboards, or any visual web interface. Prevents purple-gradient-rounded-corner-Inter-font syndrome.
author	version	origin
Mischief
1.0
Concept inspired by Anthropic frontend-design skill, rewritten for our environment
Anti-Slop Design
In all front-end tasks, it absolutely prevents obvious designs that "look like they were created by AI".

Signs of AI Slop (Absolutely Don't)
Inter/Roboto/Arial/System Font Default Use
Purple Gradient on White Background
Uniform rounded corners on all elements
Layouts that only align in the center
뻔한 hero section + 3-column features
Same shadow on all cards
Illustration without emotion
Design Process
1. Contextualize
Purpose: What problem does this interface solve?
User: Who writes? (Gamer? Developer? Ordinary person?)
Tone: Choose extreme — one of the below:
Brutalism / Minimalism / Retrofuturism / Organic Nature
Luxury / Toy / Editorial / Art Deco
Industrial / Soft Pastel / 80s Neon / Japanese Minimal
Differentiator: What is the only factor that users will remember?
2. Core Principles
Typography:

Unique and beautiful font selection (unearthed from Google Fonts)
Pairing Display Font + Text Font
Different font combinations every time (no repeating the same font)
Color:

Ensure consistency with CSS variables
Dominant color + strong accent (don't distribute evenly)
Different color palette every time
Motion:

CSS-only comes first (for HTML single files)
Staggered Reveal is the key impression when page loading
Unexpected interactions based on hover/scroll
Space Organization:

Asymmetrical layout, overlap, diagonal flow
Elements that break the grid
Generous margins or intentional density
Background/Detail:

Prohibit solid background defaults
Gradient mesh, noise texture, geometric pattern
Layers, shadows, and transparency that give a sense of depth
3. Game UI Customization Principles
Additional applications when creating game interfaces:

Immersion First: UI must be part of the game's universe
Asset Utilization: NAS Game Yard (161 GB), Gemini AI Generated, Free Asset Site
애니메이션: CSS transitions + requestAnimationFrame
Sound feedback: Sound effects when clicking/hovering (if available)
반응형: 텔레그램 Mini App 환경 고려 (WebView safe-area)
Quality Checklist
 Inter/Roboto/Arial not used?
 Is there no purple gradient?
 Aren't all elements the same border-radius?
 Are there asymmetries in the layout?
 Is it a different font/color than the one I made before?
 "Is this made by AI?" Is it enough to say that?