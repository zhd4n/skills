# Testing Inertia v3 in Laravel

Official page:

- [Testing](https://inertiajs.com/docs/v3/advanced/testing)

Use the Laravel adapter’s dedicated assertions instead of generic JSON assertions whenever the response is an Inertia response.

## Endpoint Tests

Core entrypoint:

```php
use Inertia\Testing\AssertableInertia as Assert;

$response->assertInertia(fn (Assert $page) => $page
    ->component('Users/Show')
    ->has('user')
    ->where('user.id', $user->id)
);
```

Useful assertions from the docs:

- `component()`
- `has()`
- `where()`
- `missing()`
- `etc()`
- `inertiaProps()`

## Testing Partial Reloads

Use follow-up reload assertions rather than simulating them manually:

```php
$response->assertInertia(fn (Assert $page) => $page
    ->has('orders')
    ->missing('statuses')
    ->reloadOnly('statuses', fn (Assert $reload) => $reload
        ->missing('orders')
        ->has('statuses', 5)
    )
);
```

Also available:

- `reloadExcept(...)`

## Testing Deferred Props

Use `loadDeferredProps(...)` for deferred responses:

```php
$response->assertInertia(fn (Assert $page) => $page
    ->missing('permissions')
    ->loadDeferredProps(fn (Assert $reload) => $reload
        ->has('permissions')
    )
);
```

The docs also support loading specific deferred groups by name or by array of group names.

## Testing Flash Data

Rendered Inertia responses:

```php
$response->assertInertia(fn (Assert $page) => $page
    ->hasFlash('message')
    ->hasFlash('notification.type', 'success')
    ->missingFlash('error')
);
```

Redirect responses:

```php
$response->assertRedirect('/dashboard')
    ->assertInertiaFlash('message')
    ->assertInertiaFlash('message', 'User created!')
    ->assertInertiaFlashMissing('error');
```

## Practical Laravel Advice

- Use endpoint tests for controller and prop-contract coverage.
- Use browser tests for critical end-to-end behavior such as redirects, prefetch-driven flows, and optimistic UI.
- When a feature uses deferred props, flash data, or partial reloads, assert those mechanics explicitly instead of only asserting the final happy-path page shape.
