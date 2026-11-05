-- Run this once in MySQL Workbench (or: mysql -u root -p < setup_mysql.sql)
-- Then put the same user / password in your .env file.

CREATE DATABASE IF NOT EXISTS kb_chatbot CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'kbuser'@'localhost'  IDENTIFIED BY 'ChangeThisPassword123';
CREATE USER IF NOT EXISTS 'kbuser'@'127.0.0.1' IDENTIFIED BY 'ChangeThisPassword123';

-- If the user already existed, make sure the password matches this file:
ALTER USER 'kbuser'@'localhost'  IDENTIFIED BY 'ChangeThisPassword123';
ALTER USER 'kbuser'@'127.0.0.1' IDENTIFIED BY 'ChangeThisPassword123';

GRANT ALL PRIVILEGES ON kb_chatbot.* TO 'kbuser'@'localhost';
GRANT ALL PRIVILEGES ON kb_chatbot.* TO 'kbuser'@'127.0.0.1';
FLUSH PRIVILEGES;
