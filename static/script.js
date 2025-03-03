document.getElementById('send-button').addEventListener('click', function() {
    // Get the string from the HTML
    const fileName = document.getElementById('file-container').textContent;

    // Send the string back to the Flask application using fetch
    fetch('/download', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ fileName: fileName })
    })
    .then(response => {
            if (!response.ok) {
                throw new Error('Network response was not ok');
            }
            return response.blob();
        })
    .then(blob => {
            const link = document.createElement('a');
            link.href = window.URL.createObjectURL(blob);
            link.download = fileName; // Set the desired file name
            // Append the link to the body
            document.body.appendChild(link);
            // Programmatically click the link to trigger the download
            link.click();
            // Remove the link from the document
            document.body.removeChild(link);
        })
    .catch(error => {
        console.error('Error:', error);
    });
});