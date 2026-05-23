import { Outlet, Link, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import styles from "./Layout.module.css";

import { useLogin } from "../../authConfig";
import { useChatSession } from "../../chatSessionContext";

import { LoginButton } from "../../components/LoginButton";
import headerLogo from "../../assets/pbsg_logo.png";

const Layout = () => {
    const { t } = useTranslation();
    const location = useLocation();
    const { clearChatFromHeader } = useChatSession();

    const handleHeaderClick = (e: React.MouseEvent<HTMLAnchorElement>) => {
        const onChatHome = location.pathname === "/" || location.pathname === "";
        if (onChatHome) {
            e.preventDefault();
            clearChatFromHeader();
        }
    };

    return (
        <div className={styles.layout}>
            <header className={styles.header} role={"banner"}>
                <div className={styles.headerContainer}>
                    <Link
                        to="/"
                        className={styles.headerTitleContainer}
                        onClick={handleHeaderClick}
                        aria-label={t("newChat")}
                    >
                        <img src={headerLogo} alt="Pro Bono SG" className={styles.headerLogo} />
                        <h3 className={styles.headerTitle}>{t("headerTitle")}</h3>
                    </Link>
                    <div className={styles.loginMenuContainer}>{useLogin && <LoginButton />}</div>
                </div>
            </header>

            <main className={styles.main} id="main-content">
                <Outlet />
            </main>
        </div>
    );
};

export default Layout;
