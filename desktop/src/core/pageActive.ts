import { createContext, useContext } from "react";

/** False while a kept-alive page is hidden: live previews pause instead of asking the core. */
export const PageActive = createContext(true);
export const usePageActive = () => useContext(PageActive);
