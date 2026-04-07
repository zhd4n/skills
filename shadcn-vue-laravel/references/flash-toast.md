# Flash Toasts with Sonner for Laravel Inertia v3

Use this reference when you need centralized toast notifications in a Laravel + Inertia + Vue app.

Default approach:

- use official Inertia v3 flash data: `Inertia::flash(...)` on the server
- read flash on the client from `page.flash`
- use `onFlash` for per-visit handling and `router.on('flash', ...)` for centralized listeners
- use `router.flash(...)` for client-only ephemeral flash state when no request is involved

## Server-Side Flash

Preferred modern pattern:

```php
use Inertia\Inertia;

public function store(Request $request)
{
    // ...

    return Inertia::flash([
        'toast' => [
            'type' => 'success',
            'message' => 'User created successfully!',
        ],
    ])->back();
}
```

For visit-local handling, prefer `onFlash`:

```ts
router.post('/users', data, {
  onFlash: ({ toast: flashToast }) => {
    if (!flashToast) return

    if (flashToast.type === 'success') toast.success(flashToast.message)
    else if (flashToast.type === 'error') toast.error(flashToast.message)
    else toast(flashToast.message)
  },
})
```

For client-only ephemeral state, prefer `router.flash(...)`:

```ts
router.flash({
  toast: {
    type: 'info',
    message: 'Draft restored',
  },
})
```

If the codebase already shares flash through `HandleInertiaRequests`, keep the shared prop compact and stable until you intentionally migrate:

```php
public function share(Request $request): array
{
    return array_merge(parent::share($request), [
        'flash' => [
            'success' => fn () => $request->session()->get('success'),
            'error' => fn () => $request->session()->get('error'),
            'info' => fn () => $request->session()->get('info'),
        ],
    ]);
}
```

## `useFlashToast` Composable

`toast` is imported from `vue-sonner`, not from a local component wrapper.

If you want typed `page.flash`, add a global declaration:

```ts
declare module '@inertiajs/core' {
  export interface InertiaConfig {
    flashDataType: {
      toast?: {
        type: 'success' | 'error' | 'info'
        message: string
      }
    }
  }
}
```

```ts
import { router, usePage } from '@inertiajs/vue3'
import { computed, onMounted, onUnmounted, watch } from 'vue'
import { toast } from 'vue-sonner'

type FlashToast = {
  type: 'success' | 'error' | 'info'
  message: string
}

type FlashBag = {
  toast?: {
    type: 'success' | 'error' | 'info'
    message: string
  }
}

type LegacyFlashBag = {
  success?: string | null
  error?: string | null
  info?: string | null
}

function normalizeModernFlash(flash: FlashBag): FlashToast | undefined {
  return flash.toast
}

function normalizeLegacyFlash(flash: LegacyFlashBag): FlashToast | undefined {
  if (flash.success) return { type: 'success', message: flash.success }
  if (flash.error) return { type: 'error', message: flash.error }
  if (flash.info) return { type: 'info', message: flash.info }
}

function showFlash(flash?: FlashToast) {
  if (!flash) return

  if (flash.type === 'success') toast.success(flash.message)
  else if (flash.type === 'error') toast.error(flash.message)
  else toast(flash.message)
}

export function useFlashToast() {
  const page = usePage()
  const sharedFlash = computed(() => normalizeLegacyFlash((page.props.flash ?? {}) as LegacyFlashBag))

  watch(sharedFlash, (flash) => {
    showFlash(flash)
  }, { immediate: true })

  let removeListener: (() => void) | undefined

  onMounted(() => {
    showFlash(normalizeModernFlash((page.flash ?? {}) as FlashBag))

    removeListener = router.on('flash', (event) => {
      showFlash(normalizeModernFlash(event.detail.flash as FlashBag))
    })
  })

  onUnmounted(() => {
    removeListener?.()
  })
}
```

## Layout Integration

Call the composable from a persistent layout or top-level app shell:

```vue
<script setup lang="ts">
import { Toaster } from '@/components/ui/sonner'
import { useFlashToast } from '@/composables/use-flash-toast'

useFlashToast()
</script>

<template>
  <slot />
  <Toaster />
</template>
```

## Notes

- Keep one flash schema across the app. A `toast` object with `type` and `message` scales better than ad hoc keys.
- Clean up event listeners in non-persistent layouts.
- Prefer `onFlash` when the logic belongs to one request and one component.
- If a page only needs a single inline flash message, a dedicated toast composable is unnecessary.
