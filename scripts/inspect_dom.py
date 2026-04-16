import bs4
import json

soup = bs4.BeautifulSoup(open('tv_dom_dump.html', encoding='utf-8'), 'lxml')

legend_items = soup.find_all('div', attrs={'data-qa-id': 'legend-source-item'})

for item in legend_items:
    # Pobierz główny tytuł wskaźnika
    title_el = item.find('div', attrs={'data-qa-id': 'title-wrapper legend-source-title'})
    if not title_el:
        continue
        
    title_text = title_el.get_text(strip=True)
    if 'HTS Panel' in title_text or 'PCA-RI' in title_text or 'PCA Risk' in title_text:
        print(f"\n--- Wskaźnik: {title_text} ---")
        
        # Pobierz wszystkie wartości z tego wskaźnika
        values_wrapper = item.find('div', attrs={'data-qa-id': 'legend-source-values'})
        if values_wrapper:
            value_items = values_wrapper.find_all('div', class_=lambda c: c and 'valueItem' in c)
            for val_item in value_items:
                text = val_item.get_text(strip=True)
                # Sprawdź kolor (czasami jest w stylu inline color: rgb(...))
                color = ""
                value_title_el = val_item.find('div', class_=lambda c: c and 'valueTitle' in c)
                if value_title_el and value_title_el.has_attr('style'):
                    color = value_title_el['style']
                
                value_val_el = val_item.find('div', class_=lambda c: c and 'valueValue' in c)
                if value_val_el and value_val_el.has_attr('style'):
                    color = value_val_el['style']
                    
                print(f"Wartość: '{text}', Kolor/Styl: '{color}'")
        else:
            print("Nie znaleziono data-qa-id='legend-source-values'. Zrzucam całą zawartość tekstową:")
            print([s for s in item.stripped_strings])
            
            # Spróbujmy znaleźć divy z wartościami
            print("Sprawdzam inner divs z klasą 'value':")
            for div in item.find_all('div'):
                classes = div.get('class', [])
                if any('value' in c.lower() for c in classes):
                    color = div.get('style', '')
                    print(f"  Klasy: {classes}, Tekst: '{div.get_text(strip=True)}', Styl: '{color}'")
