--
-- PostgreSQL - Odoo Settings for Test Environment
--

SET default_transaction_read_only = off;

SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;

UPDATE res_company as c SET
    c.name = v.name
FROM (values
    (1, '*TEST* Fussballclub Thalwil'),
    (2, '*TEST* Hauptverein'),
    (3, '*TEST* Juniorenabteilung'),
    (4, '*TEST* Seniorenabteilung'),
    (5, '*TEST* Clubhaus')
) AS v(id, name)
WHERE c.id = v.id;

UPDATE res_partner SET name = "*TEST* Fussballclub Thalwil" where name = "Fussballclub Thalwil";

UPDATE res_users SET active = true WHERE login in ('adrian', 'michi_test');

UPDATE club_club SET name = '*TEST* Fussballclub Thalwil' WHERE id = 1;

