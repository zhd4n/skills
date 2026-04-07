# Forms and Data for Inertia v3 Laravel Apps

Use this reference when the question is about data flow rather than visual components.

Official pages:

- [Forms](https://inertiajs.com/docs/v3/the-basics/forms)
- [Flash Data](https://inertiajs.com/docs/v3/data-props/flash-data)
- [Deferred Props](https://inertiajs.com/docs/v3/data-props/deferred-props)
- [Merging Props](https://inertiajs.com/docs/v3/data-props/merging-props)
- [Partial Reloads](https://inertiajs.com/docs/v3/data-props/partial-reloads)
- [Shared Data](https://inertiajs.com/docs/v3/data-props/shared-data)
- [Remembering State](https://inertiajs.com/docs/v3/data-props/remembering-state)
- [File Uploads](https://inertiajs.com/docs/v3/the-basics/file-uploads)

## Form Primitive Selection

- Use `<Form>` when HTML inputs with `name` attributes are enough and the page visit is the desired outcome.
- Use `useForm` when the component owns reactive form state, transforms, keyed form history state, uploads, or cancellation.
- Use `useHttp` only when you explicitly do not want a page visit.

## Form Capabilities Worth Using

- `<Form>` and `useForm` support optimistic updates.
- Forms support Precognition directly. Prefer built-in `validate`, `invalid`, `valid`, `validating`, `touch`, and `touched` over a second validation abstraction unless the project already standardizes on one.
- Forms with files automatically convert payloads to `FormData`.
- Upload progress belongs to Inertia form state and events, not to a parallel transport wrapper.
- Form data and errors can be persisted in history state with keys when a modal, drawer, or multi-step flow must survive navigation.

## Flash Data

The v3 flash-data flow is separate from ordinary page props:

- flash data is available on `page.flash`
- it does not persist in history state
- you may respond to it with the global `flash` event or per-visit `onFlash`

Laravel pattern:

```php
use Inertia\Inertia;

return Inertia::flash([
    'toast' => [
        'type' => 'success',
        'message' => 'User created successfully!',
    ],
])->back();
```

Vue pattern:

```vue
<script setup lang="ts">
import { usePage } from '@inertiajs/vue3'

const page = usePage()
</script>

<template>
  <div v-if="page.flash.toast">
    {{ page.flash.toast.message }}
  </div>
</template>
```

## Deferred, Partial, and Merged Props

Use the lightest prop strategy that matches the UX:

- shared data: truly global, low-churn data needed on every page
- flash data: one-time transient data
- deferred props: data that can arrive after the first paint
- partial reloads: refresh only selected props
- merged props: append or merge server-returned collections without replacing the whole structure

Good fit examples:

- dashboard shell first, analytics later: deferred props
- table filters changed, list only: partial reload
- infinite scroll or feed append: merge semantics

## Laravel-Specific Advice

- Keep `HandleInertiaRequests` for truly shared data, not for every temporary UI concern.
- Prefer small, explicit prop contracts from controllers over dumping full Eloquent models into page props.
- When using merged or deferred props, reflect that contract in tests with the dedicated adapter helpers instead of generic array assertions.
