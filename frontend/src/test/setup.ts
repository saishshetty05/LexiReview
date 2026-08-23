import "@testing-library/jest-dom/vitest";

// jsdom doesn't implement matchMedia -- needed by useTheme's getInitialTheme()
// for any test that renders a component tree wrapped in ThemeProvider.
if (!window.matchMedia) {
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }) as unknown as MediaQueryList;
}
