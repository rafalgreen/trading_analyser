# Ochrona gałęzi `main` na GitHubie

Repozytorium: **rafalgreen/trading_analyser**  
Domyślna gałąź: **`main`** (potwierdzone przez `git remote show origin` → `HEAD branch: main`).

## Rekomendacja: klasyczna reguła (Branch protection rule)

Dla prostego przypadku (jedna gałąź `main`, blokada force push i usuwania) użyj **klasycznej reguły**, nie rulesetów.

### Kroki w UI GitHub (2026)

1. Otwórz repozytorium: https://github.com/rafalgreen/trading_analyser  
2. **Settings** → w lewym menu **Branches** (sekcja „Code and automation”).  
3. W sekcji **Branch protection rules** kliknij **Add rule** (lub **Add branch protection rule**).  
   - Jeśli widzisz tylko **Rulesets**, przełącz się na zakładkę / link **Branch protection rules** (klasyczne reguły) albo wybierz **Add classic branch protection rule**.
4. W polu **Branch name pattern** wpisz dokładnie:

   ```
   main
   ```

   - To **nie** jest glob typu `main/*` ani `*/main`.
   - Nie używaj `master`, jeśli domyślna gałąź to `main`.
   - Nie wpisuj samego `*` — chroni to wszystkie gałęzie i łatwo o pomyłkę.

5. Zaznacz co najmniej:
   - **Block force pushes**
   - **Block deletions**

6. Opcjonalnie (jeśli nie potrzebujesz review/CI na start):
   - **Require a pull request before merging** — odznaczone
   - **Require status checks to pass** — odznaczone

7. Kliknij **Create** / **Save changes**.

Po zapisaniu reguła powinna pojawić się na liście jako pattern **`main`**.

---

## Alternatywa: Rulesets

Jeśli organizacja wymusza **Rulesets** zamiast klasycznych reguł:

1. **Settings** → **Rules** → **Rulesets** → **New ruleset** → **New branch ruleset**.  
2. **Target branches**: wybierz **Include default branch** albo ręcznie dodaj gałąź **`main`**.  
3. Włącz:
   - **Block force pushes**
   - **Block branch deletions**
4. Zapisz ruleset i upewnij się, że status rulesetu to **Active** i obejmuje **`main`**.

---

## Typowe błędy („Branch name pattern nie działa”)

| Błąd | Poprawnie |
|------|-----------|
| `main/*` | `main` |
| `*/main` | `main` |
| `*` (wszystkie gałęzie) | `main` |
| `master` przy domyślnej gałęzi `main` | `main` |
| Ruleset bez „Include default branch” / bez targetu `main` | Cel: domyślna gałąź lub `main` |
| Edycja rulesetu w innym repo / fork | Otwórz **rafalgreen/trading_analyser** |
| Brak uprawnień admina | Potrzebne uprawnienia **Admin** do repo |

---

## Weryfacja

- **Settings** → **Branches**: reguła z patternem **`main`** i zielonym statusem.  
- Próba force push na `main` powinna zostać odrzucona przez GitHub.

## API (gdy masz `gh`)

```bash
gh api repos/rafalgreen/trading_analyser/branches/main/protection -X PUT \
  --input - <<'EOF'
{
  "required_status_checks": null,
  "enforce_admins": false,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
EOF
```

Wymaga zainstalowanego [GitHub CLI](https://cli.github.com/) i `gh auth login`.
