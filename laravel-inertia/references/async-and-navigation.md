# Async and Navigation Patterns for Inertia v3 Laravel Apps

Official pages:

- [HTTP Requests](https://inertiajs.com/docs/v3/the-basics/http-requests)
- [Optimistic Updates](https://inertiajs.com/docs/v3/the-basics/optimistic-updates)
- [Layouts](https://inertiajs.com/docs/v3/the-basics/layouts)
- [Manual Visits](https://inertiajs.com/docs/v3/the-basics/manual-visits)
- [Instant Visits](https://inertiajs.com/docs/v3/the-basics/instant-visits)
- [Prefetching](https://inertiajs.com/docs/v3/data-props/prefetching)
- [Events](https://inertiajs.com/docs/v3/advanced/events)
- [Upgrade Guide for v3.0](https://inertiajs.com/docs/v3/getting-started/upgrade-guide)

## `useHttp`

Use `useHttp` for standalone HTTP requests that do not trigger page visits. The forms docs explicitly describe it as having the same developer experience as `useForm`.

Reach for it when:

- async combobox search should stay on the current page
- inline autosave should not replace the page object
- background actions fetch or mutate isolated data

Do not replace normal page visits with `useHttp` just because it feels lower-level.

## Optimistic Updates

Inertia v3 gives optimistic updates to both `<Form>` and `useForm`. Use this for:

- switches and toggles
- favorite or archive buttons
- inline row actions in tables
- small mutations where immediate visual feedback matters

Keep rollback logic close to the mutation. Avoid spreading speculative state through unrelated stores.

## Layouts and Layout Props

Use official layout features before inventing app-shell state:

- persistent layouts for shells that must survive navigation
- nested layouts for layered shells
- default layouts in `createInertiaApp`
- layout props for titles, active nav, sidebar visibility, and similar shell state
- `useLayoutProps()` when a page needs to coordinate with the active layout

Important layout facts from the docs:

- `createInertiaApp({ layout: ... })` can set a default layout
- page-level `layout` takes precedence over that default
- callback layouts may return a full layout definition or props only when a default layout exists
- `useLayoutProps()` is the documented hook for page/layout data sharing in v3

## Events and Error Boundaries

In v3, prefer official router events instead of custom wrappers:

- `flash` for centralized temporary data handling
- `error` for validation errors on successful visits
- `httpException` for non-Inertia responses such as HTML or vanilla JSON
- `networkError` for transport failures and component-resolution failures
- `navigate` for successful visits and history navigation

`httpException` and `networkError` are cancelable. Use that to keep the user on the current page when you intentionally handle the failure in place.

Per-visit callbacks should match the same concepts:

- `onFlash`
- `onHttpException`
- `onNetworkError`

## Prefetching and Cache Strategy

The docs support:

- `<Link prefetch>`
- `prefetch="click"` and `prefetch="mount"`
- `:prefetch="['mount', 'hover']"`
- `cache-for`
- `router.prefetch(...)`
- `usePrefetch()`
- `cache-tags`
- `invalidate-cache-tags`
- `router.flushAll()`, `router.flush()`, `router.flushByCacheTags()`

Use prefetch for:

- likely-next nav items
- tabs where the next click is predictable
- list/detail hover or mount flows

Do not enable prefetch blindly. Choose cache tags up front so invalidation after mutations is obvious.

## Upgrade Traps

- Replace `router.cancel()` with `router.cancelAll()`.
- Remove the old `future` config block.
- Keep bootstrap ESM-only.
- Treat `@inertiajs/vite` as the owner of modern page-resolution and dev SSR integration if the app uses it.
