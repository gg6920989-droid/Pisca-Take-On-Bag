git clone https://github.com/<твой-логин>/Pisca-Take-On-Bag.git
cd Pisca-Take-On-Bag

# удалить старый файл
rm index.html

# создать новый index.html с HTML-кодом
nano index.html   # или другой редактор
# вставь туда содержимое из webapp/index.html

git add index.html
git commit -m "fix: replace Python with real HTML for GitHub Pages"
git push origin main
