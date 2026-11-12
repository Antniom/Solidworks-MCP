`studio.bundle.js` is generated (unminified, for reviewability): it is `../host/studio.js` bundled with the A2UI web renderer
(`@a2ui/lit` and `@a2ui/web_core` 0.15.0, Apache-2.0, https://github.com/a2ui-project/a2ui) and their
dependencies (lit, zod and signal libraries). Third-party licence notices are in `studio.bundle.js.LEGAL.txt`.

Rebuild with `npm install && npm run build` in `../host`. Do not edit the bundle by hand.
