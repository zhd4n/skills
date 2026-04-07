---
name: laravel-inertia
description: Use when making app-level Inertia decisions in a Laravel application, especially around layouts, flash data, deferred props, prefetching, standalone HTTP requests, and Laravel adapter testing.
---

# Inertia v3 for Laravel

Use this skill for app-level Inertia decisions in Laravel. It complements `$shadcn-vue-laravel`, which should stay focused on shadcn-vue component wiring rather than broader Inertia architecture.

## Source of Truth

Prefer official Inertia v3 documentation. These are the pages this skill is built around:

- [Upgrade Guide for v3.0](https://inertiajs.com/docs/v3/getting-started/upgrade-guide)
- [Forms](https://inertiajs.com/docs/v3/the-basics/forms)
- [HTTP Requests](https://inertiajs.com/docs/v3/the-basics/http-requests)
- [Optimistic Updates](https://inertiajs.com/docs/v3/the-basics/optimistic-updates)
- [Layouts](https://inertiajs.com/docs/v3/the-basics/layouts)
- [Flash Data](https://inertiajs.com/docs/v3/data-props/flash-data)
- [Prefetching](https://inertiajs.com/docs/v3/data-props/prefetching)
- [Deferred Props](https://inertiajs.com/docs/v3/data-props/deferred-props)
- [Merging Props](https://inertiajs.com/docs/v3/data-props/merging-props)
- [Partial Reloads](https://inertiajs.com/docs/v3/data-props/partial-reloads)
- [Events](https://inertiajs.com/docs/v3/advanced/events)
- [Testing](https://inertiajs.com/docs/v3/advanced/testing)

## Pick the Right Primitive

- Full page navigation or standard server mutation: use `<Link>`, `router.visit`, `<Form>`, or `useForm`.
- Standalone async request with no page visit: use `useHttp`.
- Temporary one-time server data: use `Inertia::flash(...)`, then `page.flash`, `onFlash`, or the global `flash` event.
- Shared shell metadata such as page title or sidebar visibility: use default layouts, layout props, and the documented `useLayoutProps()` hook.
- Expensive props that can arrive later: use `Inertia::defer(...)` and partial reloads.
- List/detail speedups or likely-next navigation: use prefetching, `usePrefetch`, and cache tags.
- Immediate local UI change before the server confirms: use optimistic updates instead of ad hoc rollback logic.
- Feature or controller verification in Laravel tests: use the Laravel adapter assertions from the testing docs.

## Laravel-Specific v3 Rules

- Treat Inertia v3 packages as ESM-only. Do not reintroduce `require()` into app bootstrap or Vite config.
- Expect an ES2022 baseline. If older browsers matter, solve that in Vite or deployment, not by downgrading patterns.
- `router.cancelAll()` replaced `router.cancel()`.
- The old `future` config block is gone. Remove it rather than trying to carry it forward.
- Initial page data now comes from a `<script type="application/json">` element. Do not rebuild the old `data-page` flow.
- For default layouts, prefer `createInertiaApp({ layout: ... })` or mutation in the `resolve` callback.
- For dynamic layout state, use layout props and `useLayoutProps()`, not ad hoc globals.

## What This Skill Covers

- Load [`references/forms-and-data.md`](references/forms-and-data.md) for forms, flash data, deferred props, merge semantics, partial reloads, and state persistence.
- Load [`references/async-and-navigation.md`](references/async-and-navigation.md) for `useHttp`, optimistic updates, layouts, events, prefetching, and cancellation behavior.
- Load [`references/testing.md`](references/testing.md) for Laravel adapter endpoint tests, flash assertions, partial reload assertions, and deferred-prop assertions.

## Guardrails

- Do not mix page visits and standalone HTTP requests without a clear reason. If the UI expects a new page object, stay inside Inertia visits.
- Do not solve flash messages with shared props by default in new v3 code. Prefer the dedicated flash-data flow.
- Do not hardcode prefetch everywhere. Use it where user intent is strong and invalidation is understood.
- Do not invent a custom layout state channel when layout props already cover the case.
- Do not test deferred props or flash data with generic array assertions when the Laravel adapter already provides explicit helpers.
