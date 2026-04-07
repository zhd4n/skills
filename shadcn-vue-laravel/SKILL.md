---
name: shadcn-vue-laravel
description: Use when adapting shadcn-vue components or examples to a Laravel + Inertia + Vue application, especially when examples assume Nuxt, Rails, src-based paths, or vee-validate-first forms.
---

# shadcn-vue for Laravel Inertia

Use this skill as the translation layer from generic shadcn-vue examples to a Laravel + Inertia + Vue 3 codebase. Optimize for Laravel starter-kit conventions: Blade root view, `@inertiajs/vue3`, TypeScript, Tailwind, and frontend code under `resources/js`.

For broader Inertia application behavior such as `useHttp`, layout props, optimistic updates, deferred props, prefetch policy, router events, and Laravel adapter testing, use the companion skill `$laravel-inertia`.

## Quick Translation Rules

- Nuxt data APIs such as `useFetch` and `useAsyncData`: replace with controller props, deferred props, partial reloads, or explicit Inertia visits.
- `useRouter()` from Nuxt or Vue Router: replace with `router` from `@inertiajs/vue3`.
- `<NuxtLink>` or `<RouterLink>`: replace with `<Link>` from `@inertiajs/vue3`.
- `useHead()`: replace with `<Head>` from `@inertiajs/vue3`.
- `src/...`, `app/frontend/...`, or Rails frontend paths: translate to the existing Laravel structure, usually `resources/js/...`.
- shadcn-vue form examples built around `vee-validate`: do not copy them blindly into Inertia `<Form>` flows.

## Setup

1. Start from a working Laravel + Inertia + Vue application.
2. Run `npx shadcn-vue@latest init`.
3. Keep generated components under `resources/js/components/ui` unless the project already uses a different component root.
4. Ensure `@/*` resolves to `./resources/js/*` in `tsconfig.json`.
5. If the project uses `@` imports and `vite.config.ts` does not already define the alias, add one that points to `resources/js`.
6. Preserve the page and layout casing already used by the project. Do not rename `Pages` to `pages`, or the reverse, just to match an example.
7. Keep Inertia boot files ESM-only. Do not introduce `require()` into `app.ts`, SSR entrypoints, or Vite config.

If you need the Vite alias, use:

```ts
import path from 'node:path'
import { defineConfig } from 'vite'

export default defineConfig({
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'resources/js'),
    },
  },
})
```

If the app adopts `@inertiajs/vite` in v3, let that plugin own page resolution and dev SSR plumbing instead of copying older manual bootstrap patterns back into the project.

## Choose `<Form>` vs `useForm`

- Use Inertia `<Form>` for straightforward server-driven forms where inputs can submit through `name` attributes.
- Use `useForm` when the component owns reactive state, conditional fields, client-side transforms, file uploads, or progressive validation.
- `v-model` is correct with `useForm`.
- `v-model` alone is not the source of truth for an Inertia `<Form>`; the submitted payload comes from input `name` attributes.
- Do not use shadcn-vue `FormField`, `FormItem`, `FormLabel`, or `FormMessage` inside an Inertia `<Form>` unless you intentionally wire `vee-validate` for that page. They are not part of the default Laravel + Inertia path.
- In v3, `useForm().processing` remains `true` until `onFinish`, so disable buttons and pending UI from `processing` instead of resetting eagerly.

## Default `<Form>` Pattern

Use plain shadcn-vue inputs plus manual error rendering:

```vue
<script setup lang="ts">
import { Form } from '@inertiajs/vue3'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
</script>

<template>
  <Form method="post" action="/users">
    <template #default="{ errors, processing }">
      <div class="space-y-4">
        <div>
          <Label for="name">Name</Label>
          <Input id="name" name="name" />
          <p v-if="errors.name" class="text-sm text-destructive">{{ errors.name }}</p>
        </div>

        <div>
          <Label for="email">Email</Label>
          <Input id="email" name="email" type="email" />
          <p v-if="errors.email" class="text-sm text-destructive">{{ errors.email }}</p>
        </div>

        <Button type="submit" :disabled="processing">
          {{ processing ? 'Creating...' : 'Create User' }}
        </Button>
      </div>
    </template>
  </Form>
</template>
```

Inside `<Form>`, `Select` must carry a `name` prop:

```vue
<template>
  <Select name="role" default-value="member">
    <SelectTrigger><SelectValue placeholder="Select role" /></SelectTrigger>
    <SelectContent>
      <SelectItem value="admin">Admin</SelectItem>
      <SelectItem value="member">Member</SelectItem>
    </SelectContent>
  </Select>
</template>
```

## Default `useForm` Pattern

Switch to `useForm` when local reactive state is the point:

```vue
<script setup lang="ts">
import { useForm } from '@inertiajs/vue3'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'

const form = useForm({
  name: '',
  email: '',
  role: 'member',
})

const submit = () => form.post('/users')
</script>

<template>
  <form class="space-y-4" @submit.prevent="submit">
    <div>
      <Label for="name">Name</Label>
      <Input id="name" v-model="form.name" />
      <p v-if="form.errors.name" class="text-sm text-destructive">{{ form.errors.name }}</p>
    </div>

    <div>
      <Label for="email">Email</Label>
      <Input id="email" v-model="form.email" type="email" />
      <p v-if="form.errors.email" class="text-sm text-destructive">{{ form.errors.email }}</p>
    </div>

    <Button type="submit" :disabled="form.processing">
      {{ form.processing ? 'Creating...' : 'Create User' }}
    </Button>
  </form>
</template>
```

## Shadcn-Relevant v3 Helper

`useFormContext()` lets deep child components read the parent `<Form>` state. Use it for reusable shadcn wrappers, sticky action bars, field summaries, or nested submit controls without prop drilling.

For broader Inertia primitives such as Precognition, cache invalidation, router error events, deferred props, and prefetch strategy, load `$laravel-inertia` instead of expanding this skill.

Example child component using `useFormContext()`:

```vue
<script setup lang="ts">
import { useFormContext } from '@inertiajs/vue3'
import { Button } from '@/components/ui/button'

const form = useFormContext()
</script>

<template>
  <div v-if="form" class="flex items-center gap-2">
    <span v-if="form.isDirty" class="text-sm text-muted-foreground">Unsaved changes</span>
    <Button type="button" :disabled="form.processing" @click="form.submit()">
      Save
    </Button>
  </div>
</template>
```

## Component Gotchas

- Use `<Link>` instead of raw `<a>` for internal navigation that should preserve Inertia behavior.
- For table sorting, filtering, tabs, and dialogs driven by URL state, use `router.get()` or `router.reload()` with `preserveState` and `preserveScroll` when appropriate.
- `usePage()` is reactive; wrap derived values in `computed(() => page.props...)` instead of destructuring once.
- shadcn-vue `Dialog` emits `update:open`, not `close`.
- Translate Rails layout snippets to `resources/views/app.blade.php`.
- If a component pattern depends on global router listeners or visit callbacks, verify the exact event names in `$laravel-inertia` before copying older snippets.

## Flash Toasts

Prefer the Inertia v3 flash-data flow: server-side `Inertia::flash(...)`, then `page.flash`, per-visit `onFlash`, or `router.on('flash', ...)` on the client. Use `router.flash(...)` for client-only ephemeral UI state when no server round trip is needed. Treat `props.flash` through `HandleInertiaRequests` as a compatibility path for older code, not the default for new work.

Read [`references/flash-toast.md`](references/flash-toast.md) when implementing centralized Sonner toasts or a reusable flash composable. Do not load it for pages that only render one flash value inline.

## Dark Mode

`npx shadcn-vue@latest init` gives you the CSS variables. The Laravel-specific part is preventing FOUC in `resources/views/app.blade.php` before `@vite(...)` runs:

```blade
<script>
  document.documentElement.classList.toggle(
    'dark',
    localStorage.appearance === 'dark' ||
      (!('appearance' in localStorage) && window.matchMedia('(prefers-color-scheme: dark)').matches),
  );
</script>
```

Use a small `useAppearance` composable and toggle the `.dark` class on `<html>`. Do not pull in Nuxt color-mode or Rails-specific theme helpers.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `FormField` or `FormMessage` crash | shadcn-vue form components were copied without `vee-validate` context | Use plain `Input` / `Label` plus Inertia error rendering, or intentionally wire `vee-validate` |
| `Select` value is missing on submit | `<Select>` inside `<Form>` has no `name` prop | Add `name="field"` |
| `v-model` value never reaches the server | `<Form>` is reading DOM field names, not your local refs | Use `name` attributes or switch to `useForm` |
| Shared props look stale after navigation | A `usePage()` value was destructured once | Wrap derived values in `computed()` |
| Dialog close handler never fires | `@close` was used instead of `@update:open` | Handle `@update:open="(open) => { if (!open) close() }"` |
| Global error listener never runs | v2 event names were copied into a v3 app | Replace `invalid` with `httpException` and `exception` with `networkError` |
| Dark mode flashes on first paint | Theme script runs after Vite assets | Put the script in `resources/views/app.blade.php` before `@vite(...)` |

## References

- Load [`references/components.md`](references/components.md) when you need extended patterns such as AlertDialog, Sheet, Tabs, DropdownMenu, Pagination, Search, Date Picker, or Breadcrumbs.
- Load [`references/flash-toast.md`](references/flash-toast.md) when you need centralized flash-toast wiring with Sonner.
