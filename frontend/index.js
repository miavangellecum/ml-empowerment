async function uploadReceipt(fileInput) {
  const formData = new FormData();
  formData.append("file", fileInput.files[0]);

  const res = await fetch("http://localhost:8000/extract", {
    method: "POST",
    body: formData
  });
  const data = await res.json();
  console.log(data);
}