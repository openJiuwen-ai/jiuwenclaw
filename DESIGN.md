---
name: JiuwenSwarm Web
description: A calm, information-dense AI workbench with matched light and dark themes.
colors:
  primary-light: "#2563eb"
  primary-light-hover: "#3b82f6"
  primary-dark: "#60a5fa"
  primary-dark-hover: "#93c5fd"
  surface-page-light: "#f5f7f9"
  surface-card-light: "#ffffff"
  surface-panel-light: "#ffffff"
  surface-shell-light: "#f3f3f3"
  surface-page-dark: "#0f1320"
  surface-card-dark: "#171f30"
  surface-panel-dark: "#101728"
  surface-elevated-dark: "#1a2234"
  text-primary-light: "#191919"
  text-secondary-light: "#777777"
  text-primary-dark: "#e4e4e7"
  text-strong-dark: "#fafafa"
  border-light: "#e4e4e7"
  border-dark: "#243049"
  control-thumb: "#ffffff"
  sidebar-action-hover-light: "#e6e6e6"
  sidebar-action-hover-dark: "#1e2028"
  success-light: "#16a34a"
  success-dark: "#22c55e"
  warning-light: "#d97706"
  warning-dark: "#f59e0b"
  danger-light: "#dc2626"
  danger-dark: "#ef4444"
typography:
  display:
    fontFamily: "Microsoft YaHei, PingFang SC, Segoe UI, sans-serif"
    fontSize: "16px"
    fontWeight: 700
    lineHeight: 1.4
    letterSpacing: "-0.02em"
  body:
    fontFamily: "Microsoft YaHei, PingFang SC, Segoe UI, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "-0.02em"
  label:
    fontFamily: "Microsoft YaHei, PingFang SC, Segoe UI, sans-serif"
    fontSize: "12px"
    fontWeight: 500
    lineHeight: 1.4
  mono:
    fontFamily: "SFMono-Regular, SF Mono, Menlo, Monaco, Consolas, monospace"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.55
rounded:
  sm: "6px"
  md: "8px"
  lg: "12px"
  xl: "16px"
  full: "9999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "12px"
  lg: "16px"
  xl: "24px"
components:
  button-primary-light:
    backgroundColor: "{colors.primary-light}"
    textColor: "{colors.surface-card-light}"
    rounded: "{rounded.md}"
    padding: "9px 16px"
  button-primary-dark:
    backgroundColor: "{colors.primary-dark}"
    textColor: "{colors.surface-page-dark}"
    rounded: "{rounded.md}"
    padding: "9px 16px"
  card-light:
    backgroundColor: "{colors.surface-card-light}"
    textColor: "{colors.text-primary-light}"
    rounded: "{rounded.lg}"
    padding: "20px"
  card-dark:
    backgroundColor: "{colors.surface-card-dark}"
    textColor: "{colors.text-primary-dark}"
    rounded: "{rounded.lg}"
    padding: "20px"
  input-light:
    backgroundColor: "{colors.surface-card-light}"
    textColor: "{colors.text-primary-light}"
    rounded: "{rounded.md}"
    padding: "8px 12px"
  input-dark:
    backgroundColor: "{colors.surface-card-dark}"
    textColor: "{colors.text-primary-dark}"
    rounded: "{rounded.md}"
    padding: "8px 12px"
  sidebar-compact-action-hover-light:
    backgroundColor: "{colors.sidebar-action-hover-light}"
    rounded: "{rounded.sm}"
    size: "24px"
  sidebar-compact-action-hover-dark:
    backgroundColor: "{colors.sidebar-action-hover-dark}"
    rounded: "{rounded.sm}"
    size: "24px"
  switch-thumb:
    backgroundColor: "{colors.control-thumb}"
    rounded: "{rounded.full}"
    size: "16px"
---

# Design System: JiuwenSwarm Web

## 1. Overview

**Creative North Star: "The Calm Operations Desk"**

JiuwenSwarm is an information-dense AI workbench. Its visual system should feel calm, precise, and operational: navigation stays quiet, the active task is visually dominant, and status colors communicate meaning without turning the interface into a dashboard of competing accents.

This document is the design-system source of truth for `jiuwenswarm/channels/web/frontend`. It stays at the repository root so Codex, Impeccable, Stitch-compatible tools, and repository-wide agents can discover it without path-specific configuration.

Light and dark modes are equal implementations of one semantic system. Product code consumes the same `--color-*` and `--effect-*` roles in both modes; only files under `src/styles/themes/default/` assign concrete color values. Theme selection is represented by `data-theme="default"` and `data-color-mode="light|dark"`. The stored user preference remains `system`, `light`, or `dark`.

**Key Characteristics:**

- Restrained blue primary accent with teal used only as a secondary brand role.
- Layered surfaces establish hierarchy before borders and shadows.
- Compact typography and controls support long-running work sessions.
- Semantic feedback colors remain distinct in both color modes.
- Existing product density, sizing, and layout are preserved across theme changes.

## 2. Colors

The palette uses cool blue-tinted dark surfaces and neutral light surfaces, with one primary blue action family and explicit semantic feedback roles.

### Primary

- **Focused Work Blue, light** (`#2563eb`): primary actions, selected controls, links, and focus borders in light mode.
- **Visible Work Blue, dark** (`#60a5fa`): the same semantic role, raised in lightness for dark-mode legibility.

### Secondary

- **Operational Teal, light** (`#0d9488`): rare secondary brand accents and supporting visualizations.
- **Operational Teal, dark** (`#14b8a6`): dark-mode counterpart with increased visibility.

### Neutral

- **Workspace Mist** (`#f5f7f9`): light page background.
- **Workspace White** (`#ffffff`): light cards, panels, popovers, and content surfaces.
- **Night Workspace** (`#0f1320`): dark page background.
- **Night Panel** (`#101728`): dark navigation and tool-panel surface.
- **Night Card** (`#171f30`): dark content card and popover surface.
- **Primary Ink** (`#191919`): light-mode primary text.
- **Primary Mist** (`#e4e4e7`): dark-mode primary text.
- **Secondary Text** (`#777777`): secondary labels in both themes.

### Named Rules

**The Semantic Role Rule.** Components use role tokens such as `--color-text-primary`, `--color-surface-card`, and `--color-feedback-danger`; they do not use palette names or literal colors.

**The Theme Ownership Rule.** Concrete product UI colors and color-bearing effects belong in `src/styles/themes/default/light.css` or `dark.css`. A2UI rendering, exported share images, identity/avatar palettes, QR codes, file-type icons, and data-visualization palettes are intentional exceptions.

## 3. Typography

**Display Font:** Microsoft YaHei, with PingFang SC and Segoe UI fallbacks<br>
**Body Font:** Microsoft YaHei, with PingFang SC and Segoe UI fallbacks<br>
**Label/Mono Font:** SFMono-Regular, SF Mono, Menlo, Monaco, Consolas

**Character:** The typography is compact and utilitarian. Weight and spacing distinguish hierarchy while preserving room for conversation history, tools, tasks, and configuration controls.

### Hierarchy

- **Display** (700, 16px, 1.4): primary panel and conversation headings.
- **Headline** (700, 16px, 1.4): navigation section titles and modal titles.
- **Title** (600, 14px, 1.45): cards, groups, and task summaries.
- **Body** (400, 14px, 1.55): messages, settings, and explanatory content; prose should stay within 75ch when layout permits.
- **Label** (500, 12–13px, 1.4): metadata, toolbar actions, status labels, and compact controls.
- **Mono** (400, 13px, 1.55): code, paths, token counts, and technical values.

### Named Rules

**The Density Without Compression Rule.** Reduce decoration before reducing legibility; body copy does not drop below 14px and compact metadata does not drop below 11px.

## 4. Elevation

The system combines tonal layering with restrained shadows. Panels and cards are separated primarily by surface color and borders. Shadows are reserved for floating menus, popovers, focused composer surfaces, and overlays.

### Shadow Vocabulary

- **Low lift** (`0 1px 2px`): compact controls and small raised actions.
- **Medium lift** (`0 4px 12px`): composer and floating control groups.
- **High lift** (`0 12px 28px`): popovers and major floating panels.
- **Overlay lift** (`0 24px 48px`): dialogs and top-level overlays.
- **Focus ring** (two-stage surface and primary ring): keyboard focus without changing layout.

### Named Rules

**The Layer First Rule.** Use surface tokens and a border before adding a shadow. Shadows communicate actual elevation, not decoration.

## 5. Components

### Buttons

- **Shape:** compact rounded rectangle (`8px`) for standard actions; pill radii are reserved for segmented controls and small filters.
- **Primary:** `--color-action-primary` with `--color-action-primary-text`, `9px 16px` padding.
- **Hover / Focus:** primary hover token and `--effect-focus-ring` for keyboard focus. State changes are immediate unless a motion rule explicitly allows animation.
- **Secondary / Ghost:** secondary surface or transparent background with semantic text and border roles.

### Switches

- **Thumb:** `--color-control-thumb` remains fixed white in both color modes so it stays distinct from enabled and disabled tracks.
- **Track:** enabled, disabled, hover, and focus states use their own semantic roles; never derive the thumb from a card or panel surface.

### Chips

- **Style:** subtle semantic background, matching semantic text, and optional status dot.
- **State:** selected chips use the primary or relevant feedback role; unselected chips remain neutral.

### Cards / Containers

- **Corner Style:** `12px` by default; `16px` for prominent composer and dialog surfaces.
- **Background:** surface role appropriate to page, panel, card, or popover hierarchy.
- **Shadow Strategy:** flat at rest unless the element is floating.
- **Border:** `--color-border-default`; stronger border roles appear on hover or focus.
- **Internal Padding:** 12–20px according to density.

### Inputs / Fields

- **Style:** card surface, input-border role, `8px` radius, and `8px 12px` padding.
- **Focus:** `--color-border-focus` plus `--effect-focus-ring`.
- **Error / Disabled:** danger roles for errors; disabled state lowers opacity without replacing semantic text colors.

### Navigation

The 64px icon rail is visually quiet. Active and hover surfaces come from theme-owned sidebar tokens. The conversation sidebar and right tool panel use panel/shell surfaces and preserve their border, width, spacing, and typography between modes.

Compact sidebar actions are `24px` square with a `5px` radius. Section-level create actions, per-project create actions, and the project-row more action share `--color-sidebar-action-hover`. Session-row more and pin actions continue to use `--color-action-secondary`; dropdown menu items keep their own hover role.

**The Sidebar Action Boundary Rule.** Share a hover role only when controls operate at the same navigation level. Project and section actions belong together; session-row actions and menu items remain separate.

### Chat Composer

The composer is the primary interaction surface. It uses theme-owned composer surface, border, shadow, focus, and action roles while retaining a consistent `24px` outer radius and stable layout in both modes.

## 6. Motion

Motion is opt-in. Do not add CSS transitions, Tailwind transition utilities, or animation effects by default. Static state changes are the product default, including hover, active, focus, disabled, selected, loading, and theme changes.

Animation is allowed only when the user explicitly asks for it, or when it is required to explain a spatial state change that would otherwise be unclear. In that case, document the reason in the implementation notes and keep the effect scoped to the specific interaction.

**The No Default Motion Rule.** If a requirement does not mention motion, do not introduce motion.

## 7. Do's and Don'ts

### Do:

- **Do** consume colors through `--color-*` semantic roles and color-bearing effects through `--effect-*` roles.
- **Do** add every new product color to both `light.css` and `dark.css`, even when the initial values are identical.
- **Do** keep typography, radius, spacing, and motion tokens in `src/styles/foundation.css`.
- **Do** preserve `system`, `light`, and `dark` as the user-facing theme preferences.
- **Do** verify both color modes in the browser after changing Tailwind mappings; restart the dev server when `tailwind.config.js` changes.
- **Do** use `--color-sidebar-action-hover` for section create, per-project create, and project-row more actions.
- **Do** use `--color-control-thumb` for switch thumbs in every color mode.
- **Do** keep UI state changes immediate unless motion is explicitly requested or justified by the Motion rules.

### Don't:

- **Don't** add literal product UI colors to components, Tailwind class strings, or inline styles.
- **Don't** reintroduce legacy tokens such as `--bg`, `--card`, `--accent`, `--text`, or `--border` outside the explicitly excluded share-image stylesheet.
- **Don't** add compatibility aliases for removed legacy tokens.
- **Don't** encode a color name such as blue or gray into a component-facing token; name the intended role.
- **Don't** change identity/avatar, QR, file-type, A2UI, export-image, or data-visualization palettes as part of the product theme without a separate design decision.
- **Don't** apply the project/section action hover role to session-row actions or dropdown menu items.
- **Don't** use card or panel surface tokens for switch thumbs.
- **Don't** add CSS transitions, Tailwind transition utilities, or decorative animation as a default implementation habit.
